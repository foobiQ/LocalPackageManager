#!/usr/bin/env python2

""" Python 2.6 based package manager. """

import optparse
import json
import os
import sys
import stat
import subprocess
import tarfile
import shutil
from urllib2 import urlopen, HTTPError
from urlparse import urljoin
import re


_emptyConfig = {'packageManagerDir': '~/local/packageManager',
        'packageRepositoryURL': 'http://',
        'installationEnvironmentVariables': {'LPM_INSTALL_PREFIX': '~/local'},
        }

_availablePackagesDir = 'availablePackages'
_installedPackagesDir = 'installedPackages'
_sourcesDir = 'sources'
_installScriptsDir = 'installScripts'
_buildDir = 'build'

# read metadata from __init__.py
_pmDir = os.path.dirname(os.path.realpath(__file__))
_initFilePath = os.path.join(_pmDir, '__init__.py')
with open(_initFilePath) as initFile:
    _metadata = dict(re.findall("__([a-z]+)__ = '([^']+)'", initFile.read()))

class PackageError(StandardError): pass
class PackageManagerError(StandardError): pass


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
            # TODO
            pass
        else:
            raise ValueError("unknown package type {0}".format(self.packageType))

    def __str__(self):
        return '{0} ({1})'.format(self.name, self.version)

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
        unpackTo = os.path.join(buildPath, self.name)
        if os.path.isdir(unpackTo):
            shutil.rmtree(unpackTo)
        os.makedirs(unpackTo)
        try:
            tf = tarfile.open(sourceFile)
        except OSError as e:
            raise PackageError("Error while unpacking source file: {0}".format(sourceFile))
        try:
            tf.extractall(path=unpackTo)
        finally:
            tf.close()

    def _runInstallScript(self, buildPath, installScriptsPath, environmentVariables):
        installScript = os.path.abspath(os.path.join(installScriptsPath, self.installScript))
        unpackedSource = os.path.join(buildPath, self.name)
	env = os.environ
	env.update(environmentVariables)
        try:
            subprocess.check_call([installScript], cwd=unpackedSource, env=env)
        except OSError as e:
            raise PackageError("Can not call installation script: {0}".format(e))
        except subprocess.CalledProcessError as e:
            raise PackageError("Error in installation script: {0}".format(e))

    def install(self, sourcesPath, installScriptsPath, buildPath, environmentVariables):
        print "\n--- installing {0} ---".format(self)
        if self.packageType == 'archive':
            self._unpackSource(sourcesPath, buildPath)
            self._runInstallScript(buildPath, installScriptsPath, environmentVariables)
        elif self.packageType == 'meta':
            pass
        elif self.packageType == 'git':
            # TODO
            raise NotImplementedError
        else:
            raise ValueError("unknown package type {0}".format(self.packageType))


class PackageManager(object):
    def __init__(self, config):
        self._basePath = os.path.expanduser(config['packageManagerDir'])
        self._availablePackagesPath = os.path.join(self._basePath, _availablePackagesDir)
        self._installedPackagesPath = os.path.join(self._basePath, _installedPackagesDir)
        self._sourcesPath = os.path.join(self._basePath, _sourcesDir)
        self._installScriptsPath = os.path.join(self._basePath, _installScriptsDir)
        self._buildPath = os.path.join(self._basePath, _buildDir)

        # create directories if they don't exist
        for d in [self._availablePackagesPath, self._installedPackagesPath, \
                self._sourcesPath, self._installScriptsPath, self._buildPath]:
            if not os.path.isdir(d):
                os.makedirs(d)

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
        """ Return all dependencies of package recursively as a list. Direct
        dependencies are first and dependencies of dependencies come after
        those. The packages need to be installed in reversed order. Duplicates
        are possible.

        """
        allDependencies = []
        for dependencyName in package.dependencies:
            try:
                dependency = self._availablePackages[dependencyName]
            except KeyError:
                raise PackageManagerError("Can not resolve dependency '{0}' of package {1}"\
                        .format(dependencyName, package))
            allDependencies.append(dependency)
            allDependencies += self._getDependencies(dependency)
        return allDependencies

    def _getInstalledDependings(self, package):
        """ Return all packages which depend on package recursively as a list.
        Directly depending packages are first, with indirectly depending
        packages after those. Packages need to be rebuilt in normal order for
        an upgrade. Duplicates are possible.

        """
        allDependings = []
        for p in self._installedPackages.values():
            if package.name in p.dependencies:
                allDependings.append(p)
                allDependings += self._getInstalledDependings(p)
        return allDependings

    def getAvailablePackages(self):
        return self._availablePackages

    def getInstalledPackages(self):
        return self._installedPackages

    def printAvailablePackages(self):
        for p in sorted(self._availablePackages):
            print self._availablePackages[p]

    def printInstalledPackages(self):
        for p in sorted(self._installedPackages):
            print self._installedPackages[p]

    def installPackages(self, packageNames, reinstall=False, reinstallDependencies=False):
        """ Install the list of package names with dependencies.

        """
        packagesToInstall = []
        for packageName in reversed(packageNames):
            if packageName not in self._availablePackages.keys():
                print "Package '{0}' is not available".format(packageName)
                continue
            package = self._availablePackages[packageName]
            # ignore if already in the install list
            if package in packagesToInstall:
                continue
            # ignore and warn if already installed
            # if reinstall is True don't check if the packet is already installed
            if not reinstall and packageName in self._installedPackages.keys():
                installedPackage = self._installedPackages[packageName]
                print "Package '{0.name}' is already installed in version {0.version}.".format(installedPackage)
                if package.version > installedPackage.version:
                    print "Newer version ({}) is available. Please do update first.".format(package.version)
                return
            for p in reversed(self._getDependencies(package)):
                if p not in packagesToInstall:
                    # if reinstallDependencies is True don't check if the dependency is already installed
                    if reinstallDependencies:
                        packagesToInstall = [p] + packagesToInstall
                    elif p.name in self._installedPackages:
                        installedPackage = self._installedPackages[p.name]
                        if p.version > installedPackage.version:
                            print "Newer version ({}) is available for {}. Please do update first.".format(p.version, p.name)
                            return
                    else:
                        packagesToInstall = [p] + packagesToInstall
            packagesToInstall = [package] + packagesToInstall

        if packagesToInstall:
            print "The following actions will be done (in this order):"
            for p in reversed(packagesToInstall):
                if p.name not in self._installedPackages:
                    print "  {} install version {}".format(p.name, p.version)
                elif p in self._installedPackages.values():
                    print "  {} reinstall version {}".format(p.name, p.version)
                else:
                    installedVersion = self._installedPackages[p.name].version
                    print "  {} update from version {} to {}".format(p.name, installedVersion, p.version)
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
                    # install package
                    try:
                        p.install(self._sourcesPath, self._installScriptsPath, self._buildPath, \
                                self._installEnvs)
                    except PackageError as e:
                        raise PackageManagerError("Error while installing {0}: {1}".format(p, e))
                    # copy config file to installed packages directory
                    fromPath = os.path.join(self._availablePackagesPath, p.configFile)
                    toPath = os.path.join(self._installedPackagesPath, p.configFile)
                    shutil.copy(fromPath, toPath)

    def _downloadFile(self, url, destination, executable=False):
        try:
            remote = urlopen(url)
        except HTTPError as e:
            raise PackageManagerError("can not download {0}: {1}".format(url, e))
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
                print "downloading sources of {0}".format(package)
                sourceFileURL = urljoin(sourcesDirURL, sourceFile)
                localSourceFilePath = os.path.join(self._sourcesPath, sourceFile)
                self._downloadFile(sourceFileURL, localSourceFilePath)
            except AttributeError:
                pass
            # install script
            try:
                installScript = package.installScript
                print "downloading install script of {0}".format(package)
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
        packageFilePattern = re.compile('"[^"]+\.json"')
        packageFileList = map(lambda p: p.strip('"'), packageFilePattern.findall(availablePackagesHTML))
        for packageFileName in packageFileList:
            print "downloading {0}".format(packageFileName)
            packageFileURL = urljoin(availablePackagesURL, packageFileName)
            localFile = os.path.join(self._availablePackagesPath, packageFileName)
            self._downloadFile(packageFileURL, localFile)

        print "Done"

    def upgradeInstalledPackages(self):
        packagesToUpgrade = []
        for installedPackage in self._installedPackages.values():
            availablePackage = self._availablePackages[installedPackage.name]
            if availablePackage.version > installedPackage.version:
                packagesToUpgrade.append(installedPackage)
        # reinstall installed packages that depend on the packages to be upgraded
        packagesToReinstall = []
        for package in packagesToUpgrade:
            packagesToReinstall += self._getInstalledDependings(package)
        # filter out duplicates
        packageNamesToReinstall = []
        for p in reversed(packagesToUpgrade + packagesToReinstall):
            if p.name not in packageNamesToReinstall:
                packageNamesToReinstall = [p.name] + packageNamesToReinstall
        # reverse package list to update deepest dependency first and reinstall depending packages afterwards
        packageNamesToReinstall.reverse()
        # install them
        self.installPackages(packageNamesToReinstall, reinstall=True)

def main():
    supportedCommands = ['install', 'update', 'upgrade', 'listInstalled', 'listAvailable']
    usage = ("usage: %prog [options] command [package [package] ...]\n"\
              "\n"
              "command can be one of:\n"
              "  install        takes a list of packages to be installed\n"
              "  update         updates the available package list\n"
              "  upgrade        updates all installed packages to the available version\n"
              "  listInstalled  lists all installed packages with their version\n"
              "  listAvailable  lists all available packages with their version"
              .format(', '.join(supportedCommands)))
    optParser = optparse.OptionParser(usage=usage, description=__doc__)
    optParser.add_option('-c', '--config', dest='config', default='~/.pmconfig.json', \
            help="The package manager configuration file (default %default)")
    optParser.add_option('--version', dest='printVersion', action="store_true", \
            help="print the version and exit")
    optParser.add_option('--reinstall', dest='reinstall', action='store_true', default=False, \
            help="Reinstall the package if it is already installed.")
    optParser.add_option('--reinstall-deps', dest='reinstallDeps', action='store_true', default=False, \
            help="Also reinstall already installed dependencies.")
    (opts, args) = optParser.parse_args()

    if opts.printVersion:
        print _metadata['version']
        sys.exit(0)

    try:
        command = args[0]
        packages = args[1:]
    except IndexError:
        optParser.error("No command specified")

    # read config
    configFile = os.path.expanduser(opts.config)
    if not os.path.isfile(configFile):
        # config file doesn't exists, write template and exit
        print "No package manager config found at '{0}'. I have written a template. " \
                "Please edit it at '{0}'.".format(configFile)
        with open(configFile, 'w') as f:
            json.dump(_emptyConfig, f, sort_keys=True, indent=4, separators=(',', ': '))
        sys.exit(1)
    with open(configFile) as f:
        pmConfig = json.load(f)

    pm = PackageManager(pmConfig)

    if command == 'install':
        if packages:
            try:
                pm.installPackages(packages, reinstall=opts.reinstall,
                        reinstallDependencies=opts.reinstallDeps)
            except PackageManagerError as e:
                print >> sys.stderr, e
                sys.exit(-1)
        else:
            optParser.error("No packages to install provided")
    elif command == 'update':
        pm.updateAvailablePackages()
    elif command == 'upgrade':
        pm.upgradeInstalledPackages()
    elif command == 'listInstalled':
        pm.printInstalledPackages()
    elif command == 'listAvailable':
        pm.printAvailablePackages()

    else:
        optParser.error("Unsupported command: {0}".format(command))

if __name__ == '__main__':
    main()
