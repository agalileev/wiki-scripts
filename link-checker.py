#! /usr/bin/env python3

import argparse
import os.path
import re

import mwparserfromhell

from MediaWiki import API, diff_highlighted
from MediaWiki.exceptions import *
from MediaWiki.interactive import *
import ArchWiki.lang as lang

class LinkChecker:
    def __init__(self, api):
        self.api = api
        self.edit_summary = "simplification of wikilinks (testing https://github.com/lahwaacz/wiki-scripts/blob/master/link-checker.py)"
        # namespaces of the links to be checked
        self.backlink_namespaces = ["0", "4", "12"]

        # get list of pageids of redirect pages
        pageids = []
        for ns in self.backlink_namespaces:
            pages = api.generator(generator="allpages", gaplimit="max", gapfilterredir="redirects", gapnamespace=ns)
            _pageids = [str(page["pageid"]) for page in pages]
            pageids.extend(_pageids)

        # resolve redirects
        redirects = api.resolve_redirects(*pageids)

        # build dictionary
        self.redirects = {}
        for r in redirects:
            origin = r["from"]
            target = "{}#{}".format(r["to"], r["tofragment"]) if "tofragment" in r else r["to"]
            self.redirects[origin] = target

    def check_trivial(self, wikilink):
        """
        Perform trivial simplification, replace `[[Foo|foo]]` with `[[foo]]`.

        :param wikilink: instance of `mwparserfromhell.nodes.wikilink.Wikilink`
                         representing the link to be checked
        """
        # replace underscores, Wikicode.matches() does not do this
        _link = mwparserfromhell.parse(wikilink)
        try:
            _link.replace("_", " ")
        except ValueError:
            # there is no underscore
            pass
        _link = _link.nodes[0]

        if _link.title.matches(_link.text):
            # title is mandatory, so the text becomes the title
            wikilink.title = wikilink.text
            wikilink.text = None

    def check_relative(self, wikilink, title):
        """
        Use relative links whenever possible. For example, links to sections such as
        `[[Foo#Bar]]` on a page `Foo` are replaced with `[[#Bar]]`.

        :param wikilink: instance of `mwparserfromhell.nodes.wikilink.Wikilink`
                         representing the link to be checked
        :param title: instance of `str` representing the title of the page being
                      checked
        """
        title = mwparserfromhell.parse(title)
        try:
            _title, _section = wikilink.title.split("#", maxsplit=1)
            if title.matches(_title):
                # FIXME: should be Wikicode, parse creates a list
#                wikilink.title = mwparserfromhell.wikicode.Wikicode("#" + _section)
                # space before # avoids parsing as list, it is stripped later
                wikilink.title = mwparserfromhell.parse(" #" + _section)
        except ValueError:
            # raised when unpacking failed
            pass

    def check_redirect_exact(self, wikilink):
        """
        Replace `[[foo|bar]]` with `[[bar]]` if `foo` and `bar` point to the same page
        after resolving redirects.

        :param wikilink: instance of `mwparserfromhell.nodes.wikilink.Wikilink`
                         representing the link to be checked
        """
        if wikilink.text is None:
            return

        # canonicalize link parts
        _title = wikilink.title[0].upper() + wikilink.title[1:]
        _text = wikilink.text[0].upper() + wikilink.text[1:]
        _title = _title.replace("_", " ")
        _text = _text.replace("_", " ")

        target1 = self.redirects.get(_title)
        target2 = self.redirects.get(_text)
        if target1 is not None and target2 is not None:
            if target1 == target2:
                wikilink.title = wikilink.text
                wikilink.text = None
        elif target1 is not None:
            if target1 == _text:
                wikilink.title = wikilink.text
                wikilink.text = None
        elif target2 is not None:
            if target2 == _title:
                wikilink.title = wikilink.text
                wikilink.text = None

    def collapse_whitespace(self, wikicode, wikilink):
        """
        Attempt to fix spacing around wiki links after the substitutions.
        """
        parent, _ = wikicode._do_strong_search(wikilink, True)
        index = parent.index(wikilink)

        def _get_text(index):
            try:
                node = parent.get(index)
                if not isinstance(node, mwparserfromhell.nodes.text.Text):
                    return None
                return node
            except IndexError:
                return None

        prev = _get_text(index - 1)
        next_ = _get_text(index)

        if prev is not None and prev.endswith(" "):
            if wikilink.title.startswith(" "):
                wikilink.title = wikilink.title.lstrip()
        if next_ is not None and next_.startswith(" "):
            if wikilink.text is not None and wikilink.text.endswith(" "):
                wikilink.text = wikilink.text.rstrip()
            elif wikilink.text is None and wikilink.title.endswith(" "):
                wikilink.title = wikilink.title.rstrip()

    def update_page(self, title, text):
        """
        Parse the content of the page and call various methods to update the links.

        :param title: title of the page (as `str`)
        :param text: content of the page (as `str`)
        :returns: updated content (as `str`)
        """
        print("Parsing '%s'..." % title)
        wikicode = mwparserfromhell.parse(text)

        for wikilink in wikicode.ifilter_wikilinks(recursive=True):
            self.check_trivial(wikilink)
            self.check_relative(wikilink, title)
            self.check_redirect_exact(wikilink)
            # fix spacing, e.g. after substitution '[[foo | bar]]' -> '[[ bar]]'
            self.collapse_whitespace(wikicode, wikilink)

        return str(wikicode)

    def process_page(self, title):
        result = self.api.call(action="query", prop="revisions", rvprop="content|timestamp", titles=title)
        page = list(result["pages"].values())[0]
        timestamp = page["revisions"][0]["timestamp"]
        text_old = page["revisions"][0]["*"]
        text_new = self.update_page(title, text_old)
        if text_old != text_new:
            try:
                edit_interactive(self.api, page["pageid"], text_old, text_new, timestamp, self.edit_summary, bot="")
#                self.api.edit(page["pageid"], text_new, timestamp, self.edit_summary, bot="")
            except (APIError, APIWarnings):
                print("error: failed to edit page '%s'" % title)

    def process_allpages(self):
        for page in self.api.generator(generator="allpages", gaplimit="100", gapfilterredir="nonredirects", prop="revisions", rvprop="content|timestamp"):
            title = page["title"]
            if lang.detect_language(title)[1] != "English":
                continue
            timestamp = page["revisions"][0]["timestamp"]
            text_old = page["revisions"][0]["*"]
            text_new = self.update_page(title, text_old)
            if text_old != text_new:
                try:
                    edit_interactive(self.api, page["pageid"], text_old, text_new, timestamp, self.edit_summary, bot="")
#                    self.api.edit(page["pageid"], text_new, timestamp, self.edit_summary, bot="")
                except (APIError, APIWarnings):
                    print("error: failed to edit page '%s'" % title)


# any path, the dirname part must exist (e.g. path to a file that will be created in the future)
def arg_dirname_must_exist(string):
    dirname = os.path.split(string)[0]
    if not os.path.isdir(dirname):
        raise argparse.ArgumentTypeError("directory '%s' does not exist" % dirname)
    return string

# path to existing directory
def arg_existing_dir(string):
    if not os.path.isdir(string):
        raise argparse.ArgumentTypeError("directory '%s' does not exist" % string)
    return string


if __name__ == "__main__":
    argparser = argparse.ArgumentParser(description="Update Pkg/AUR templates")

    _api = argparser.add_argument_group(title="API parameters")
    _api.add_argument("--api-url", default="https://wiki.archlinux.org/api.php", metavar="URL",
            help="the URL to the wiki's api.php (default: %(default)s)")
    _api.add_argument("--cookie-path", type=arg_dirname_must_exist, default=os.path.expanduser("~/.cache/ArchWiki.bot.cookie"), metavar="PATH",
            help="path to cookie file (default: %(default)s)")
    _api.add_argument("--ssl-verify", default=1, choices=(0, 1),
            help="whether to verify SSL certificates (default: %(default)s)")

    args = argparser.parse_args()

    # retype from int to bool
    args.ssl_verify = True if args.ssl_verify == 1 else False

    api = API(args.api_url, cookie_file=args.cookie_path, ssl_verify=args.ssl_verify)
    # ensure that we are authenticated
    require_login(api)

    checker = LinkChecker(api)
#    checker.process_page("NVIDIA")
    checker.process_allpages()
