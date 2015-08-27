#!/usr/bin/env python

import json
import os.path
import subprocess
import tarfile
import shutil


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
        return self._versionStr == other._versionStr


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
            raise ValueError('unknown package type {}'.format(self.packageType))

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

    def _runInstallScript(self, buildPath, installScriptsPath):
        installScript = os.path.abspath(os.path.join(installScriptsPath, self.installScript))
        if not os.path.isfile(installScript):
            raise PackageError("Installation script '{}' of package '{}' doesn't exist".\
                    format(installScript, self))
        # TODO check if script is executable
        unpackedSource = os.path.join(buildPath, self.name)
        subprocess.check_call([installScript], cwd=unpackedSource)

    def install(self, sourcesPath, installScriptsPath, buildPath):
        print "\n=== installing {} ===".format(self)
        if self.packageType == 'archive':
            self._unpackSource(sourcesPath, buildPath)
            self._runInstallScript(buildPath, installScriptsPath)
        elif self.packageType == 'meta':
            pass
        elif self.packageType == 'git':
            # TODO
            pass
        else:
            raise ValueError('unknown package type {}'.format(self.packageType))


class PackageManager(object):
    def __init__(self, config):
        self._basePath = config['basePath']
        self._availablePackagesPath = os.path.join(self._basePath, 'availablePackages')
        self._installedPackagesPath = os.path.join(self._basePath, 'installedPackages')
        self._sourcesPath = os.path.join(self._basePath, 'sources')
        self._installScriptsPath = os.path.join(self._basePath, 'installScripts')
        self._buildPath = os.path.join(self._basePath, 'build')

        #self._installEnvs = config['installEnvironmentVariables']

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

    def installPackage(self, packageName):
        if packageName in self._installedPackages.keys():
            print "Package '{0.name}' is already installed in version {0.version}"\
                    .format(self._installedPackages[packageName])
        elif packageName in self._availablePackages.keys():
            package = self._availablePackages[packageName]
            packageDependencies = [package] + self._getDependencies(package)
            packagesToInstall = []
            for p in reversed(packageDependencies):
                if p not in self._installedPackages.values() and p not in packagesToInstall:
                    packagesToInstall = [p] + packagesToInstall

            print 'The following packages will be installed to satisfy dependencies:'
            for s in packagesToInstall:
                print '  {}'.format(s)
            print '\nare you sure? [y/n]'
            # TODO ask user

            for p in reversed(packagesToInstall):
                p.install(self._sourcesPath, self._installScriptsPath, self._buildPath)
                fromPath = os.path.join(self._availablePackagesPath, p.configFile)
                toPath = os.path.join(self._installedPackagesPath, p.configFile)
                shutil.copy(fromPath, toPath)
        else:
            print "Package '{}' is not available".format(packageName)

    def selfUpdate(self):
        raise NotImplementedError

    def updateInstalledPackages(self):
        for packageName in self._installedPackages:
            installedPackage = self._installedPackages[packageName]
            availablePackage = self._availablePackages[packageName]
            if availablePackage.version > installedPackage.version:
                availablePackage.install(self._sourcesPath, self._installScriptsPath, self._buildPath)
                # check for installed packages that depend on it and need to be recompiled
                for p in self._installedPackages:
                    if packageName in p.dependencies:
                        p.install(self._sourcesPath, self._installScriptsPath, self._buildPath)


