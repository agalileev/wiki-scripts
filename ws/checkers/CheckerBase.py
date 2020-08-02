#! /usr/bin/env python3

import mwparserfromhell

import ws.ArchWiki.lang as lang
from ws.utils import LazyProperty
from ws.parser_helpers.title import canonicalize
from ws.parser_helpers.wikicode import get_parent_wikicode, get_adjacent_node

__all__ = ["localize_flag", "CheckerBase"]

def localize_flag(wikicode, node, template_name):
    """
    If a ``node`` in ``wikicode`` is followed by a template with the same base
    name as ``template_name``, this function changes the adjacent template's
    name to ``template_name``.

    :param wikicode: a :py:class:`mwparserfromhell.wikicode.Wikicode` object
    :param node: a :py:class:`mwparserfromhell.nodes.Node` object
    :param str template_name: the name of the template flag, potentially
                              including a language name
    """
    parent = get_parent_wikicode(wikicode, node)
    adjacent = get_adjacent_node(parent, node, ignore_whitespace=True)

    if isinstance(adjacent, mwparserfromhell.nodes.Template):
        adjname = lang.detect_language(str(adjacent.name))[0]
        basename = lang.detect_language(template_name)[0]
        if canonicalize(adjname) == canonicalize(basename):
            adjacent.name = template_name


class CheckerBase:
    def __init__(self, api, db):
        self.api = api
        self.db = db

    @LazyProperty
    def _alltemplates(self):
        result = self.api.generator(generator="allpages", gapnamespace=10, gaplimit="max", gapfilterredir="nonredirects")
        return {page["title"].split(":", maxsplit=1)[1] for page in result}

    def get_localized_template(self, template, language="English"):
        assert(canonicalize(template) in self._alltemplates)
        localized = lang.format_title(template, language)
        if canonicalize(localized) in self._alltemplates:
            return localized
        # fall back to English
        return template
