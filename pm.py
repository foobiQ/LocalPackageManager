#!/usr/bin/env python2

""" Python2 based package manager. """

import argparse
import json
import os
import stat
import subprocess
import tarfile
import shutil
from urllib2 import urlopen
from urlparse import urljoin
import re


emptyConfig = {'packageManagerDir': '~/local/packageManager',
        'packageRepositoryURL': 'http://',
        'installationEnvironmentVariables': {'INSTALL_PREFIX': '~/local'},
        }

_availablePackagesDir = 'availablePackages'
_installedPackagesDir = 'installedPackages'
_sourcesDir = 'sources'
_installScriptsDir = 'installScripts'
_buildDir = 'build'


class PackageError(StandardError): pass


class Version(object):
    def __init__(self, s):
        self._versionStr = s

    @classmethod
    def fromStr(cls, s):
        cls(s)

    def __str__(self):
        return self._versionStr

    def __eq__(self, other):
        if not isinstance(other, Version):
            return NotImplemented
        return self._versionStr == other._versionStr

    def __gt__(self, other):
        if not isinstance(other, Version):
            return NotImplemented
        # TODO real version comparison
        return self._versionStr != other._versionStr


class Package(object):
    def __init__(self, config):
        self.name = config['name']
        self.version = Version(config['version'])
        self.packageType = config['type']
        self.dependencies = config['dependencies']
        if self.packageType == 'archive':
            self.sourceFile = config['sourceFile']
            self.installScript = config['installScript']
        elif self.packageType == 'meta':
            pass
        elif self.packageType == 'git':
            pass
            #raise NotImplementedError
        else:
            raise ValueError("unknown package type {}".format(self.packageType))

    def __str__(self):
        return '{} ({})'.format(self.name, self.version)

    def __eq__(self, other):
        if not isinstance(other, Package):
            return NotImplemented
        return (self.name == other.name and self.version == other.version)

    def __ne__(self, other):
        return not self.__eq__(other)

    @classmethod
    def fromJsonFile(cls, jsonFile):
        with open(jsonFile) as f:
            obj = cls(json.load(f))
            obj.configFile = os.path.basename(jsonFile)
            return obj

    def _unpackSource(self, sourcesPath, buildPath):
        sourceFile = os.path.join(sourcesPath, self.sourceFile)
        if not os.path.isfile(sourceFile):
            raise PackageError("Source file '{}' of package '{}' doesn't exist".\
                    format(sourceFile, self))
        unpackTo = os.path.join(buildPath, self.name)
        if os.path.isdir(unpackTo):
            shutil.rmtree(unpackTo)
        os.makedirs(unpackTo)
        with tarfile.open(sourceFile) as tf:
            tf.extractall(path=unpackTo)

    def _runInstallScript(self, buildPath, installScriptsPath, environmentVariables):
        installScript = os.path.abspath(os.path.join(installScriptsPath, self.installScript))
        if not os.path.isfile(installScript):
            raise PackageError("Installation script '{}' of package '{}' doesn't exist".\
                    format(installScript, self))
        unpackedSource = os.path.join(buildPath, self.name)
        try:
            subprocess.check_call([installScript], cwd=unpackedSource, env=environmentVariables)
        except OSError as e:
            raise PackageError("Can not call installation script: {}".format(e))

    def install(self, sourcesPath, installScriptsPath, buildPath, environmentVariables):
        print "\n--- installing {} ---".format(self)
        if self.packageType == 'archive':
            self._unpackSource(sourcesPath, buildPath)
            self._runInstallScript(buildPath, installScriptsPath, environmentVariables)
        elif self.packageType == 'meta':
            pass
        elif self.packageType == 'git':
            # TODO
            pass
        else:
            raise ValueError("unknown package type {}".format(self.packageType))

    def downloadSource(self, sourcesPath):
        raise NotImplementedError


class PackageManager(object):
    def __init__(self, config):
        self._basePath = os.path.expanduser(config['packageManagerDir'])
        self._availablePackagesPath = os.path.join(self._basePath, _availablePackagesDir)
        self._installedPackagesPath = os.path.join(self._basePath, _installedPackagesDir)
        self._sourcesPath = os.path.join(self._basePath, _sourcesDir)
        self._installScriptsPath = os.path.join(self._basePath, _installScriptsDir)
        self._buildPath = os.path.join(self._basePath, _buildDir)

        self._installEnvs = config['installationEnvironmentVariables']
        self.packageRepoURL = config['packageRepositoryURL']

        self._availablePackages = self._readPackageConfigs(self._availablePackagesPath)
        self._installedPackages = self._readPackageConfigs(self._installedPackagesPath)

    def _readPackageConfigs(self, configsPath):
        packages = {}
        for packageConfigFile in os.listdir(configsPath):
            if not packageConfigFile.startswith('.'):
                p = Package.fromJsonFile(os.path.join(configsPath, packageConfigFile))
                packages[p.name] = p
        return packages

    def _getDependencies(self, package):
        allDependencies = []
        for dependencyName in package.dependencies:
            try:
                dependency = self._availablePackages[dependencyName]
            except KeyError:
                raise PackageError("Can not resolve dependency '{}'".format(dependencyName))
            allDependencies.append(dependency)
            allDependencies += self._getDependencies(dependency)
        return allDependencies

    def getAvailablePackages(self):
        return self._availablePackages

    def getInstalledPackages(self):
        return self._installedPackages

    def installPackages(self, packageNames):
        packagesToInstall = []
        for packageName in packageNames:
            if packageName in self._installedPackages.keys():
                print "Package '{0.name}' is already installed in version {0.version}"\
                        .format(self._installedPackages[packageName])
            elif packageName in self._availablePackages.keys():
                package = self._availablePackages[packageName]
                packageDependencies = [package] + self._getDependencies(package)
                for p in reversed(packageDependencies):
                    if p not in self._installedPackages.values() and p not in packagesToInstall:
                        packagesToInstall = [p] + packagesToInstall
            else:
                print "Package '{}' is not available".format(packageName)

        if packagesToInstall:
            print "The following packages will be installed to satisfy dependencies:"
            for s in packagesToInstall:
                print "  {}".format(s)
            print
            doIt = raw_input("are you sure? [y/n]\n")
            if doIt.lower() == 'y':
                print
                print "=== downloading packages ==="
                print
                self.downloadPackages(packagesToInstall)
                print
                print "=== installing packages ==="
                for p in reversed(packagesToInstall):
                    p.install(self._sourcesPath, self._installScriptsPath, self._buildPath, \
                            self._installEnvs)
                    fromPath = os.path.join(self._availablePackagesPath, p.configFile)
                    toPath = os.path.join(self._installedPackagesPath, p.configFile)
                    shutil.copy(fromPath, toPath)

    def _downloadFile(self, url, destination, executable=False):
        remote = urlopen(url)
        try:
            with open(destination, 'w') as local:
                local.write(remote.read())
            if executable:
                st = os.stat(destination)
                os.chmod(destination, st.st_mode | stat.S_IEXEC)
        finally:
            remote.close()

    def downloadPackages(self, packages):
        sourcesDirURL = urljoin(self.packageRepoURL, _sourcesDir + '/')
        installScriptsDirURL = urljoin(self.packageRepoURL, _installScriptsDir + '/')
        for package in packages:
            # source file
            try:
                sourceFile = package.sourceFile
                print "downloading sources of {}".format(package)
                sourceFileURL = urljoin(sourcesDirURL, sourceFile)
                localSourceFilePath = os.path.join(self._sourcesPath, sourceFile)
                self._downloadFile(sourceFileURL, localSourceFilePath)
            except AttributeError:
                pass
            # install script
            try:
                installScript = package.installScript
                print "downloading install script of {}".format(package)
                installScriptURL = urljoin(installScriptsDirURL, installScript)
                localScriptPath = os.path.join(self._installScriptsPath, installScript)
                self._downloadFile(installScriptURL, localScriptPath, executable=True)
            except AttributeError:
                pass

    def updateAvailablePackages(self):
        print "Updating available packages repository"
        availablePackagesURL = urljoin(self.packageRepoURL, _availablePackagesDir + '/')
        availablePackagesPath = urlopen(availablePackagesURL)
        availablePackagesHTML = availablePackagesPath.read().decode('utf-8')
        packageFilePattern = re.compile('".*\.json"')

        packageFileList = map(lambda p: p.strip('"'), packageFilePattern.findall(availablePackagesHTML))
        for packageFileName in packageFileList:
            print "downloading {}".format(packageFileName)
            packageFileURL = urljoin(availablePackagesURL, packageFileName)
            localFile = os.path.join(self._availablePackagesPath, packageFileName)
            self._downloadFile(packageFileURL, localFile)

        print "Done"

    def upgradeInstalledPackages(self):
        raise NotImplementedError
        for packageName in self._installedPackages:
            installedPackage = self._installedPackages[packageName]
            availablePackage = self._availablePackages[packageName]
            if availablePackage.version > installedPackage.version:
                availablePackage.install(self._sourcesPath, self._installScriptsPath, self._buildPath)
                # check for installed packages that depend on it and need to be recompiled
                for p in self._installedPackages:
                    if packageName in p.dependencies:
                        p.install(self._sourcesPath, self._installScriptsPath, self._buildPath)

def main():
    supportedCommands = ['install', 'update', 'upgrade']
    argParser = argparse.ArgumentParser(description=__doc__, \
            formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    argParser.add_argument('command', choices=supportedCommands)
    argParser.add_argument('packages', nargs='*', metavar='package', \
            help="If command==install, the packages to be installed")
    argParser.add_argument('-c', '--config', default='~/.pmconfig.json', \
            help="The package manager configuration file.")
    args = argParser.parse_args()

    # read config
    # TODO check if config file exists, write template and exit if not
    with open(os.path.expanduser(args.config)) as f:
        pmConfig = json.load(f)

    pm = PackageManager(pmConfig)

    if args.command == 'install':
        if len(args.packages) > 0:
            pm.installPackages(args.packages)
        else:
            argParser.error('No packages to install provided')
    elif args.command == 'update':
        pm.updateAvailablePackages()
    elif args.command == 'upgrade':
        pm.upgradeInstalledPackages()

if __name__ == '__main__':
    main()
