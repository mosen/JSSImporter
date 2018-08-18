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


from collections import OrderedDict
from distutils.version import StrictVersion
import os
from zipfile import ZipFile, ZIP_DEFLATED
import sys
from xml.etree import ElementTree
from xml.sax.saxutils import escape

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


class JSSPolicy(Processor):
    """Adds a Policy object to configured JAMF Pro Servers.

    This processor should always run AFTER a JSSPackage processor, so that it knows which package it is associated
    with.

    Optionally, creates supporting policy categories, policy,
        and self service icon.

        File paths to support files are searched for in order:
            1. Path as specified.
            2. The parent folder of the path.
            3. First ParentRecipe's folder.
            4. First ParentRecipe's parent folder.
            5. Second ParentRecipe's folder.
            6. Second ParentRecipe's parent folder.
            7. Nth ParentRecipe's folder.
            8. Nth ParentRecipe's parent folder.
    """
    input_variables = {
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
        "policy_category": {
            "required": False,
            "description":
                "Category to create/associate policy with. Defaults"
                " to 'No category assigned'.",
        },
        "force_policy_state": {
            "required": False,
            "description":
                "If set to False JSSImporter will not override the policy "
                "enabled state. This allows creating new policies in a default "
                "state and then going and manually enabling them in the JSS "
                "Boolean, defaults to 'True'",
            "default": True,
        },
        "groups": {
            "required": False,
            "description":
                "Array of group dictionaries. Wrap each group in a "
                "dictionary. Group keys include 'name' (Name of the group to "
                "use, required), 'smart' (Boolean: static group=False, smart "
                "group=True, default is False, not required), and "
                "template_path' (string: path to template file to use for "
                "group, required for smart groups, invalid for static groups)",
        },
        "exclusion_groups": {
            "required": False,
            "description":
                "Array of group dictionaries. Wrap each group in a "
                "dictionary. Group keys include 'name' (Name of the group to "
                "use, required), 'smart' (Boolean: static group=False, smart "
                "group=True, default is False, not required), and "
                "template_path' (string: path to template file to use for "
                "group, required for smart groups, invalid for static groups)",
        },
        "scripts": {
            "required": False,
            "description":
                "Array of script dictionaries. Wrap each script in "
                "a dictionary. Script keys include 'name' (Name of the script "
                "to use, required), 'template_path' (string: path to template "
                "file to use for script, required)",
        },
        "extension_attributes": {
            "required": False,
            "description":
                "Array of extension attribute dictionaries. Wrap each "
                "extension attribute in a dictionary. Script keys include: "
                "'ext_attribute_path' (string: path to extension attribute "
                "file.)",
        },
        "policy_template": {
            "required": False,
            "description":
                "Filename of policy template file. If key is "
                "missing or value is blank, policy creation will be skipped.",
            "default": "",
        },
        "policy_action_type": {
            "required": False,
            "description":
                "Type of policy 'package_configuration' to perform. Must be "
                "one of 'Install', 'Cache', 'Install Cached'.",
            "default": "Install",
        },
        "self_service_description": {
            "required": False,
            "description":
                "Use to populate the %SELF_SERVICE_DESCRIPTION% variable for "
                "use in templates. Primary use is for filling the info button "
                "text in Self Service, but could be used elsewhere.",
            "default": "",
        },
        "self_service_icon": {
            "required": False,
            "description":
                "Path to an icon file. Use to add an icon to a "
                "self-service enabled policy. Because of the way Casper "
                "handles this, the JSSImporter will only upload if the icon's "
                "filename is different than the one set on the policy (if it "
                "even exists). Please see the README for more information.",
            "default": "",
        },
        "site_id": {
            "required": False,
            "description": "ID of the target Site",
        },
        "site_name": {
            "required": False,
            "description": "Name of the target Site",
        },
    }
    output_variables = {
        "jss_changed_objects": {
            "description": "Dictionary of added or changed values."
        },
        "jss_importer_summary_result": {
            "description": "Description of interesting results."
        },
    }
    description = __doc__

    def __init__(self, env=None, infile=None, outfile=None):
        """Sets attributes here."""
        super(JSSPolicy, self).__init__(env, infile, outfile)
        self.jss = None
        self.pkg_name = None
        self.prod_name = None
        self.version = None
        self.policy_category = None
        self.replace_dict = {}

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

        self.output("JSSImporter version: %s." % __version__)

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

        self.policy_category = self.handle_category("policy_category")

        # Build our text replacement dictionary
        self.build_replace_dict()

        self.extattrs = self.handle_extension_attributes()

        self.groups = self.handle_groups(self.env.get("groups"))
        self.exclusion_groups = self.handle_groups(
            self.env.get("exclusion_groups"))

        self.scripts = self.handle_scripts()
        self.policy = self.handle_policy()
        self.handle_icon()

        self.summarize()

    def handle_policy(self):
        """Create or update a policy."""
        if self.env.get("policy_template"):
            template_filename = self.env.get("policy_template")
            policy = self.update_or_create_new(
                jss.Policy, template_filename, update_env="jss_policy_updated",
                added_env="jss_policy_added")
        else:
            self.output("Policy creation not desired, moving on...")
            policy = None

        return policy

    def handle_icon(self):
        """Add self service icon if needed."""
        # Icons are tricky. The only way to add new ones is to use
        # FileUploads.  If you manually upload them, you can add them to
        # a policy to get their ID, but there is no way to query the JSS
        # to see what icons are available. Thus, icon handling involves
        # several cooperating methods.  If we just add an icon every
        # time we run a recipe, however, we end up with a ton of
        # redundent icons, and short of actually deleting them in the
        # sql database, there's no way to delete icons. So when we run,
        # we first check for an existing policy, and if it exists, copy
        # its icon XML, which is then added to the templated Policy. If
        # there is no icon information, but the recipe specifies one,
        # then FileUpload it up.

        # If no policy handling is desired, we can't upload an icon.
        if self.env.get("self_service_icon") and self.policy is not None:
            # Search through search-paths for icon file.
            icon_path = self.find_file_in_search_path(
                self.env["self_service_icon"])

            icon_filename = os.path.basename(icon_path)

            # Compare the filename in the policy to the one provided by
            # the recipe. If they don't match, we need to upload a new
            # icon.
            policy_filename = self.policy.findtext(
                "self_service/self_service_icon/filename")
            if not policy_filename == icon_filename:
                icon = jss.FileUpload(self.jss, "policies", "id",
                                      self.policy.id, icon_path)
                icon.save()
                self.env["jss_changed_objects"]["jss_icon_uploaded"].append(
                    icon_filename)
                self.output("Icon uploaded to JSS.")
            else:
                self.output("Icon matches existing icon, moving on...")
