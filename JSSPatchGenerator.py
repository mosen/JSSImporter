#!/usr/bin/python
# Copyright 2018 Mosen, Portions Copyright 2009-2018 Greg Neagle.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or
# implied.
#
# See the License for the specific language governing permissions and
# limitations under the License.

from distutils.version import StrictVersion
import subprocess
import os
import tempfile
import urllib2
from xml.dom import minidom

sys.path.insert(0, '/Library/Application Support/JSSImporter')
import jss
# Ensure that python-jss dependency is at minimum version
try:
    from jss import __version__ as PYTHON_JSS_VERSION
except ImportError:
    PYTHON_JSS_VERSION = "0.0.0"

from autopkglib import Processor, ProcessorError

__all__ = ["JSSPatchGenerator"]
__version__ = "1.0.0"
REQUIRED_PYTHON_JSS_VERSION = StrictVersion("2.0.0")


def extracted_package_info(pkg_path, toc, tmpdir="/tmp"):  # (str, list, str) -> Generator[Tuple[str, str]]
    """Generator function which yields extracted package information.

    Each iteration yields the absolute path to the extracted package info or distribution file, and the second item
    is either 'distribution' or 'pkginfo', to indicate the type of package info.
    """

    for entry in toc:
        cmd_extract = ['/usr/bin/xar', '-xf', pkg_path, entry]

        if entry.startswith('PackageInfo'):
            result = subprocess.call(cmd_extract)
            if result == 0:
                yield os.path.join(tmpdir, entry), 'pkginfo'
            else:
                print("Failed to extract PackageInfo")

        if entry.endswith('.pkg/PackageInfo'):
            result = subprocess.call(cmd_extract)
            if result == 0:
                yield os.path.join(tmpdir, entry), 'pkginfo'
            else:
                print("Failed to extract .pkg/PackageInfo")

        if entry.startswith('Distribution'):
            result = subprocess.call(cmd_extract)
            if result == 0:
                yield os.path.join(tmpdir, entry), 'distribution'
            else:
                print("Failed to extract Distribution file")


class JSSPatchGenerator(Processor):
    """Creates a Patch definition for a single package in json format.

    By and large the attributes that are scraped out of the package will be
    similar to that of munki's `makepkginfo`. Apologies to Greg Neagle for taking his work.

    patchinfo will override generated information from the package.

    Example `patchinfo`:

        <key>patchinfo</key>
        <dict>
            <key>id</key>
            <string>AppName</string>
            <key>name</key>
            <string>App Name</string>
            <key>publisher</key>
            <string>Publisher Name</string>
            <key>appName</key>
            <string>AppName.app</string>
            <key>bundleId</key>
            <string>com.someone.app</string>
            <key>currentVersion</key>
            <string>x.x.x.</string>
            <key>killApps</key>
            <array>
                <dict>
                    <key>bundleId</key>
                    <string>com.someone.app</string>
                    <key>appName</key>
                    <string>App Name.app</string>
                </dict>
            </array>

            <!-- individual component -->
            <key>name</key>
            <string>AppName</string>
            <key>version</key
            <string>x.x.x</string>
            <key>criteria</key>
            <!-- smart group criteria -->
            <array>
                <key>name</key>
                <string>Application Bundle ID</string>
                <key>operator</key>
                <string>is</string>
                <key>value</key>
                <string>com.app</string>
                <key>type</key>
                <string>recon</string>
                <key>and</key>
                <true/>
            </array>
            <key>capabilities</key>
            <array>
                <dict>

                </dict>
            </array>
        </dict>
    """
    input_variables = {
        "pkg_path": {
            "required": True,
            "description": "Path to a pkg or dmg to import.",
        },
        "patchinfo": {
            "required": False,
            "description": ("Dictionary of patchinfo keys to copy to "
                            "generated patch info."),
        },
    }

    def get_pkg_restartinfo(self, filename):  # type: (str) -> Optional[str]
        """Uses Apple's installer tool to get RestartAction from an installer item.
        Straight copy from munki"""
        proc = subprocess.Popen(['/usr/sbin/installer',
                                 '-query', 'RestartAction',
                                 '-pkg', filename],
                                bufsize=-1,
                                stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE)
        (out, err) = proc.communicate()
        if proc.returncode:
            self.output("installer -query failed: %s %s", out, err)
            return None

        if out:
            restart_action = str(out).rstrip('\n')
            if restart_action != 'None':
                return restart_action

        return None

    def parse_pkginfo_refs(self, pkg_info_path):  # type: (str) -> List[dict]
        """Parse a PackageInfo file, given the local path to a temporarily extracted PackageInfo"""
        info = []
        dom = minidom.parse(pkg_info_path)
        refs = dom.getElementsByTagName('pkg-info')
        if refs is None:
            return info

        for ref in refs:
            keys = ref.attributes.keys()
            if 'identifier' in keys and 'version' in keys:
                pkginfo = {
                    'packageid': ref.attributes['identifier'].value.encode('UTF-8'),
                    'version': ref.attributes['version'].value.encode('UTF-8'),
                }
                payloads = ref.getElementsByTagName('payload')
                if payloads:
                    keys = payloads[0].attributes.keys()
                    if 'installKBytes' in keys:
                        pkginfo['installed_size'] = int(
                            payloads[0].attributes[
                                'installKBytes'].value.encode('UTF-8'))
                        if pkginfo not in info:
                            info.append(pkginfo)
                            # if there isn't a payload, no receipt is left by a flat
                            # pkg, so don't add this to the info array
        return info

    def parse_distribution_refs(self, distribution_path, path_to_pkg=None):
        """Parse a Distribution file, given the local path to a temporarily extracted Distribution"""
        info = []
        pkgref_dict = {}
        dom = minidom.parse(distribution_path)
        refs = dom.getElementsByTagName('pkg-ref')
        if refs is None:
            return info

        for ref in refs:
            keys = ref.attributes.keys()
            if 'id' in keys:
                pkgid = ref.attributes['id'].value.encode('UTF-8')
                if not pkgid in pkgref_dict:
                    pkgref_dict[pkgid] = {'packageid': pkgid}
                if 'version' in keys:
                    pkgref_dict[pkgid]['version'] = \
                        ref.attributes['version'].value.encode('UTF-8')
                if 'installKBytes' in keys:
                    pkgref_dict[pkgid]['installed_size'] = int(
                        ref.attributes['installKBytes'].value.encode(
                            'UTF-8'))
                if ref.firstChild:
                    text = ref.firstChild.wholeText
                    if text.endswith('.pkg'):
                        if text.startswith('file:'):
                            relativepath = urllib2.unquote(
                                text[5:].encode('UTF-8'))
                            pkgdir = os.path.dirname(
                                path_to_pkg or distribution_path)
                            pkgref_dict[pkgid]['file'] = os.path.join(
                                pkgdir, relativepath)
                        else:
                            if text.startswith('#'):
                                text = text[1:]
                            relativepath = urllib2.unquote(
                                text.encode('UTF-8'))
                            thisdir = os.path.dirname(distribution_path)
                            pkgref_dict[pkgid]['file'] = os.path.join(
                                thisdir, relativepath)

        for key in pkgref_dict.keys():
            pkgref = pkgref_dict[key]
            if 'file' in pkgref:
                if os.path.exists(pkgref['file']):
                    info.extend(self.get_pkg_receiptinfo(pkgref['file']))
                    continue
            if 'version' in pkgref:
                if 'file' in pkgref:
                    del pkgref['file']
                info.append(pkgref_dict[key])

    def get_flat_pkg_info(self, filename):
        """Get information from a Flat package."""
        # get the absolute path to the pkg because we need to do a chdir later
        abspkgpath = os.path.abspath(filename)

        # Get the TOC of the flat pkg so we can search it later
        cmd_toc = ['/usr/bin/xar', '-tf', filename]
        proc = subprocess.Popen(cmd_toc, bufsize=-1, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE)
        (toc, err) = proc.communicate()
        toc = toc.strip().split('\n')

        tmpdir = tempfile.mkdtemp()
        for infofile_path, info_type in extracted_package_info(filename, toc, tmpdir):
            if info_type == 'pkginfo':
                self.parse_pkginfo_refs(infofile_path)
            elif info_type == 'distribution':
                self.parse_distribution_refs(infofile_path)
            else:
                continue

    def get_pkg_receiptinfo(self, filename):  # type: (str) -> Optional[List[dict]]
        self.output('Examining package {}'.format(filename))
        info = None

        if os.path.isfile(filename):  # Flat Distribution .pkg
            info = self.get_flat_pkg_info(filename)
        elif os.path.isdir(filename):  # Bundle Style .pkg
            info = self.get_bundle_pkg_info(filename)
        else:
            pass  # ERROR: unrecognised

        return info


    def get_pkg_metadata(self):
        """Get information about a .pkg, distribution pkg or .mpkg.

        Entirely derived from Greg Neagles implementation in munki tools (pkgutils.py)
        """
        restart_action = self.get_pkg_restartinfo(self.env['pkg_path'])


