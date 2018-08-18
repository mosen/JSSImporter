#!/usr/bin/python
# Copyright 2014-2017 Shea G. Craig
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
"""See docstring for JSSImporter class."""


from distutils.version import StrictVersion
import os
from zipfile import ZipFile, ZIP_DEFLATED
import sys
from xml.etree import ElementTree

sys.path.insert(0, '/Library/Application Support/JSSImporter')

import jss
# Ensure that python-jss dependency is at minimum version
try:
    from jss import __version__ as PYTHON_JSS_VERSION
except ImportError:
    PYTHON_JSS_VERSION = "0.0.0"

from autopkglib import Processor, ProcessorError


__all__ = ["JSSImporter"]
__version__ = "1.0.1"
REQUIRED_PYTHON_JSS_VERSION = StrictVersion("2.0.0")


class JSSPackage(Processor):
    """Uploads packages to configured JAMF Pro distribution points."""

    input_variables = {
        "prod_name": {
            "required": True,
            "description": "Name of the product.",
        },
        "pkg_path": {
            "required": True,
            "description":
                "Path to a pkg or dmg to import - provided by "
                "previous pkg recipe/processor.",
        },
        "version": {
            "required": False,
            "description":
                "Version number of software to import - usually provided "
                "by previous pkg recipe/processor, but if not, defaults to "
                "'0.0.0.0'. ",
            "default": "0.0.0.0"
        },
        "JSS_REPOS": {
            "required": False,
            "description":
                "Array of dicts for each intended distribution point. Each "
                "distribution point type requires slightly different "
                "configuration keys and data. Please consult the "
                "documentation. ",
            "default": [],
        },
        "JSS_URL": {
            "required": True,
            "description":
                "URL to a JSS that api the user has write access "
                "to, optionally set as a key in the com.github.autopkg "
                "preference file.",
        },
        "API_USERNAME": {
            "required": True,
            "description":
                "Username of account with appropriate access to "
                "jss, optionally set as a key in the com.github.autopkg "
                "preference file.",
        },
        "API_PASSWORD": {
            "required": True,
            "description":
                "Password of api user, optionally set as a key in "
                "the com.github.autopkg preference file.",
        },
        "JSS_VERIFY_SSL": {
            "required": False,
            "description":
                "If set to False, SSL verification in communication"
                " with the JSS will be skipped. Defaults to 'True'.",
            "default": True,
        },
        "JSS_SUPPRESS_WARNINGS": {
            "required": False,
            "description":
                "Determines whether to suppress urllib3 warnings. "
                "If you choose not to verify SSL with JSS_VERIFY_SSL, urllib3 "
                "throws warnings for each of the numerous requests "
                "JSSImporter makes. If you would like to see them, set to "
                "'False'. Defaults to 'True'.",
            "default": True,
        },
        "category": {
            "required": False,
            "description":
                "Category to create/associate imported app "
                "package with. Defaults to 'No category assigned'.",
        },
        "os_requirements": {
            "required": False,
            "description":
                "Comma-seperated list of OS version numbers to "
                "allow. Corresponds to the OS Requirements field for "
                "packages. The character 'x' may be used as a wildcard, as "
                "in '10.9.x'",
            "default": ""
        },
        "package_info": {
            "required": False,
            "description": "Text to apply to the package's Info field.",
            "default": ""
        },
        "package_notes": {
            "required": False,
            "description": "Text to apply to the package's Notes field.",
            "default": ""
        },
        "package_priority": {
            "required": False,
            "description":
                "Priority to use for deploying or uninstalling the "
                "package. Value between 1-20. Defaults to '10'",
            "default": "10"
        },
        "package_reboot": {
            "required": False,
            "description":
                "Computers must be restarted after installing the package "
                "Boolean. Defaults to 'False'",
            "default": "False"
        },
        "package_boot_volume_required": {
            "required": False,
            "description":
                "Ensure that the package is installed on the boot drive "
                "after imaging. Boolean. Defaults to 'True'",
            "default": "True"
        },
    }
    output_variables = {
        "jss_packages_added": {
            "description": "List of package names added"
        },
        "jss_packages_updated": {
            "description": "List of package names updated"
        },
        "jss_category_added": {
            "description": "List of categories added"
        }
    }
    description = __doc__

    def __init__(self, env=None, infile=None, outfile=None):
        """Sets attributes here."""
        super(JSSPackage, self).__init__(env, infile, outfile)
        self.jss = None
        self.pkg_name = None
        self.prod_name = None
        self.version = None
        self.category = None
        self.package = None

    def main(self):
        """Main processor code."""
        # Ensure we have the right version of python-jss
        python_jss_version = StrictVersion(PYTHON_JSS_VERSION)
        self.output("python-jss version: %s." % python_jss_version)
        if python_jss_version < REQUIRED_PYTHON_JSS_VERSION:
            self.output(
                "python-jss version is too old. Please update to version: %s."
                % REQUIRED_PYTHON_JSS_VERSION)
            raise ProcessorError

        self.output("JSSPackage version: %s." % __version__)

        # clear any pre-existing summary result
        if "jss_importer_summary_result" in self.env:
            del self.env["jss_importer_summary_result"]

        self.create_jss()
        self.output("JSS version: '{}'".format(self.jss.version()))

        self.pkg_name = os.path.basename(self.env["pkg_path"])
        self.prod_name = self.env["prod_name"]
        self.version = self.env.get("version")
        if self.version == "0.0.0.0":
            self.output(
                "Warning: No `version` was added to the AutoPkg env up to "
                "this point. JSSImporter is defaulting to version %s!"
                % self.version)

        # Build and init jss_changed_objects
        self.init_jss_changed_objects()

        self.category = self.handle_category("category")

        # Get our DPs read for copying.
        if len(self.jss.distribution_points) == 0:
            self.output("Warning: No distribution points configured!")
        for dp in self.jss.distribution_points:
            dp.was_mounted = hasattr(dp, 'is_mounted') and dp.is_mounted()
        # Don't bother mounting the DPs if there's no package.
        if self.env["pkg_path"]:
            self.jss.distribution_points.mount()

        self.package = self.handle_package()

        # Done with DPs, unmount them.
        for dp in self.jss.distribution_points:
            if not dp.was_mounted:
                self.jss.distribution_points.umount()

        self.summarize()

    def create_jss(self):
        """Create a JSS object for API calls"""
        kwargs = {
            'url': self.env['JSS_URL'],
            'user': self.env['API_USERNAME'],
            'password': self.env['API_PASSWORD'],
            'ssl_verify': self.env["JSS_VERIFY_SSL"],
            'repo_prefs': self.env["JSS_REPOS"]}
        self.jss = jss.JSS(**kwargs)
        if self.env.get('verbose', 1) >= 4:
            self.jss.verbose = True

    def init_jss_changed_objects(self):
        """Build a dictionary to track changes to JSS objects."""
        keys = (
            "jss_repo_updated", "jss_category_added", "jss_package_added", "jss_package_updated")
        for key in keys:
            self.env[key] = list()

    def handle_category(self, category_type, category_name=None):
        """Ensure a category is present."""
        if self.env.get(category_type):
            category_name = self.env.get(category_type)

        if category_name is not None:
            try:
                category = self.jss.Category(category_name)
                category_name = category.name
                self.output(
                    "Category type: %s-'%s' already exists according to JSS, "
                    "moving on..." % (category_type, category_name))
            except jss.GetError:
                # Category doesn't exist
                category = jss.Category(self.jss, category_name)
                category.save()
                self.output(
                    "Category type: %s-'%s' created." % (category_type,
                                                         category_name))
                self.env["jss_category_added"].append(
                    category_name)
        else:
            category = None

        return category

    def handle_package(self):
        """Creates or updates, and copies a package object.

        This will only upload a package if a file with the same name
        does not already exist on a DP. If you need to force a
        re-upload, you must delete the package on the DP first.

        Further, if you are using a JDS, it will only upload a package
        if a package object with a filename matching the AutoPkg
        filename does not exist. If you need to force a re-upload to a
        JDS, please delete the package object through the web interface
        first.
        """
        # Skip package handling if there is no package or repos.
        pkg_path = self.env["pkg_path"]
        if self.env["JSS_REPOS"] and pkg_path != "":
            # Ensure that `pkg_path` is valid.
            if not os.path.exists(pkg_path):
                raise ProcessorError(
                    "JSSImporter can't find a package at '%s'!" % pkg_path)
            os_requirements = self.env.get("os_requirements")
            package_info = self.env.get("package_info")
            package_notes = self.env.get("package_notes")
            package_priority = self.env.get("package_priority")
            package_reboot = self.env.get("package_reboot")
            package_boot_volume_required = self.env.get(
                "package_boot_volume_required")
            # See if the package is non-flat (requires zipping prior to
            # upload).
            if os.path.isdir(pkg_path):
                pkg_path = self.zip_pkg_path(pkg_path)

                # Make sure our change gets added back into the env for
                # visibility.
                self.env["pkg_path"] = pkg_path
                self.pkg_name += ".zip"

            try:
                package = self.jss.Package(self.pkg_name)
                self.output("Pkg-object already exists according to JSS, "
                            "moving on...")
                pkg_update = (self.env["jss_package_updated"])
            except jss.GetError:
                # Package doesn't exist
                package = jss.Package(self.jss, self.pkg_name)
                pkg_update = (self.env["jss_package_added"])

            if self.category is not None:
                cat_name = self.category.name
            else:
                cat_name = ""
            self.update_object(cat_name, package, "category", pkg_update)
            self.update_object(os_requirements, package, "os_requirements",
                               pkg_update)
            self.update_object(package_info, package, "info", pkg_update)
            self.update_object(package_notes, package, "notes", pkg_update)
            self.update_object(package_priority, package, "priority",
                               pkg_update)
            self.update_object(package_reboot, package, "reboot_required",
                               pkg_update)
            self.update_object(package_boot_volume_required, package,
                               "boot_volume_required", pkg_update)

            # Ensure packages are on distribution point(s)

            # If we had to make a new package object, we know we need to
            # copy the package file, regardless of DP type. This solves
            # the issue regarding the JDS.exists() method: See
            # python-jss docs for info.  The problem with this method is
            # that if you cancel an AutoPkg run and the package object
            # has been created, but not uploaded, you will need to
            # delete the package object from the JSS before running a
            # recipe again or it won't upload the package file.
            #
            # Passes the id of the newly created package object so JDS'
            # will upload to the correct package object. Ignored by
            # AFP/SMB.
            if len(self.env["jss_package_added"]) > 0:
                self.copy(pkg_path, id_=package.id)
            # For AFP/SMB shares, we still want to see if the package
            # exists.  If it's missing, copy it!
            elif not self.jss.distribution_points.exists(
                    os.path.basename(pkg_path)):
                self.copy(pkg_path)
            else:
                self.output("Package upload not needed.")
        else:
            package = None
            self.output("Package upload and object update skipped. If this is "
                        "a mistake, ensure you have JSS_REPOS configured.")

        return package

    def zip_pkg_path(self, path):
        """Add files from path to a zip file handle.

        Args:
            path (str): Path to folder to zip.

        Returns:
            (str) name of resulting zip file.
        """
        zip_name = "{}.zip".format(path)

        with ZipFile(
                zip_name, "w", ZIP_DEFLATED, allowZip64=True) as zip_handle:

            for root, _, files in os.walk(path):
                for member in files:
                    zip_handle.write(os.path.join(root, member))

            self.output("Closing: %s" % zip_name)

        return zip_name

    def summarize(self):
        """If anything has been added or updated, report back."""
        # Only summarize if something has happened.
        if 'jss_package_added' in self.env or 'jss_package_updated' in self.env:
            # Create a blank summary.
            self.env["jss_importer_summary_result"] = {
                "summary_text": "The following changes were made to the JSS:",
                "report_fields": [
                    "Name", "Package", "Categories", "Version",
                    "Package_Uploaded"],
                "data": {
                    "Name": "",
                    "Package": "",
                    "Categories": "",
                    "Version": "",
                    "Package_Uploaded": ""
                }
            }
            # TODO: This is silly. Use a defaultdict for storing changes
            # and just copy the stuff that changed.

            # Shortcut variables for lovely code conciseness
            data = self.env["jss_importer_summary_result"]["data"]

            data["Name"] = self.env.get('NAME', '')
            data["Version"] = self.env.get('version', '')

            package = self.get_report_string(self.env["jss_package_added"] +
                                             self.env["jss_package_updated"])
            if package:
                data["Package"] = package

            # Get nice strings for our list-types.
            if self.env["jss_category_added"]:
                data["Categories"] = self.get_report_string(
                    self.env["jss_category_added"])

            jss_package_uploaded = self.get_report_string(self.env["jss_repo_updated"])
            if jss_package_uploaded:
                data["Package_Uploaded"] = "True"

    def update_object(self, data, obj, path, update):
        """Update an object if it differs.

        If a value differs between the recipe and the object, update
        the object to reflect the change, and add the object to a
        summary list.

        Args:
            data: Recipe string value to enforce.
            obj: JSSObject type to set data on.
            path: String path to desired XML.
            update: Summary list object to append obj to if something
                is changed.
        """
        if data != obj.findtext(path):
            obj.find(path).text = data
            obj.save()
            self.output("%s %s updated." % (
                str(obj.__class__).split(".")[-1][:-2], path))
            update.append(obj.name)

    def copy(self, source_item, id_=-1):
        """Copy a package or script using the JSS_REPOS preference."""
        self.output("Copying %s to all distribution points." % source_item)

        def output_copy_status(connection):
            """Output AutoPkg copying status."""
            self.output("Copying to %s" % connection["url"])

        self.jss.distribution_points.copy(source_item, id_=id_,
                                          pre_callback=output_copy_status)
        self.env["jss_repo_updated"].append(
            os.path.basename(source_item))
        self.output("Copied %s" % source_item)

    def ensure_xml_structure(self, element, path):
        """Ensure that all tiers of an XML hierarchy exist."""
        search, _, path = path.partition("/")
        if search:
            if element.find(search) is None:
                ElementTree.SubElement(element, search)
            return self.ensure_xml_structure(element.find(search), path)
        return element

    def get_report_string(self, items):   # pylint: disable=no-self-use
        """Return human-readable string from a list of JSS objects."""
        return ", ".join(set(items))


if __name__ == "__main__":
    processor = JSSPackage()   # pylint: disable=invalid-name
    processor.execute_shell()
