# -*- coding: utf-8 -*-
"""
Output formatting to text via lxml xpath nodes abstracted in this file.
"""
__title__ = 'newspaper'
__author__ = 'Lucas Ou-Yang'
__license__ = 'MIT'
__copyright__ = 'Copyright 2014, Lucas Ou-Yang'

import re
from itertools import chain
from html import unescape
import logging

from .text import innerTrim


log = logging.getLogger(__name__)


class NodeTextExclusion:

    EXCLUDED_TAGS = {
        'figure',
        'figcaption',
    }
    AD_CLASSES = {
        'js_ad-mobile-dynamic',
        'js_ad-dynamic',
        'ad-mobile-dynamic',
        'movable-ad',
    }
    OBJECT_DESCRIPTIONS = {
        'bold',
        'detailImageDesc',
        'credits',
    }
    PAGE_NAVIGATION = {
        '« previous post | next post »',
        'prevnext',
    }
    IGNORED_CLASSES = {
        'audioplayer_container',
        'postfeedback',
    }
    IGNORED_TEXT = re.compile(
        'Permalink|(Share link)'
    )

    def _has_ignored_class(self, node):
        return set(node.classes).intersection(self.IGNORED_CLASSES)

    def _has_ads(self, node):
        return node.tag == 'div' and set(node.classes).intersection(self.AD_CLASSES)

    def _looks_like_object_description(self, node):
        """Some sites are using <span> for image or text descriptions, which we don't want

        Usually we can't remove <span> altogether, and there is no other hook
        to look for other than looking at the classes themselves, which feels wrong
        as we'd have to add as many classes as we'd find in the future.
        """
        return node.tag == 'span' and set(node.classes).intersection(self.OBJECT_DESCRIPTIONS)

    def _looks_like_page_navigation(self, node):
        """Heuristics against page pagination nodes"""
        if (node.text or '').strip() in self.PAGE_NAVIGATION:
            return True
        if node.tag == 'p' and dict(node.items()).get('id') in self.PAGE_NAVIGATION:
            return True
        return False

    def _is_tag_excluded(self, node):
        if node.tag in self.EXCLUDED_TAGS:
            return True
        parent = node.getparent()
        if parent and parent.tag in self.EXCLUDED_TAGS:
            return True
        return False

    def _ignored_by_content(self, node):
        if node.text:
            return bool(self.IGNORED_TEXT.search(node.text))
        return False

    def is_excluded(self, node):
        """Check if the given node should be completely excluded from the output"""
        filter_methods = [
            self._is_tag_excluded,
            self._has_ads,
            self._looks_like_object_description,
            self._looks_like_page_navigation,
            self._has_ignored_class,
            self._ignored_by_content,
        ]
        for method in filter_methods:
            if method(node):
                return True
        return False


class NodeTextExtractor:

    def __init__(self, parser):
        self._parser = parser
        self._exclusion = NodeTextExclusion()

    def extract_text(self, node, title):
        if self._exclusion.is_excluded(node):
            return None

        filtered_nodes = [
            child for child in node.iter()
            if not self._exclusion.is_excluded(child)
        ]

        text_elements = [
            innerTrim(unescape(child.text))
            for child in filtered_nodes if child.text
        ]
        for text_element in text_elements:
            parts = filter(None, text_element.split(r'\n'))
            for part in parts:
                if title and part == title:
                    continue
                yield part


class OutputFormatter(object):

    def __init__(self, config):
        self.top_node = None
        self.config = config
        self.parser = self.config.get_parser()
        self.language = config.language
        self.stopwords_class = config.stopwords_class
        self._extractor = NodeTextExtractor(self.parser)

    def update_language(self, meta_lang):
        '''Required to be called before the extraction process in some
        cases because the stopwords_class has to set incase the lang
        is not latin based
        '''
        if meta_lang:
            self.language = meta_lang
            self.stopwords_class = \
                self.config.get_stopwords_class(meta_lang)

    def get_top_node(self):
        return self.top_node

    def get_formatted(self, top_node, title=None):
        """Returns the body text of an article, and also the body article
        html if specified. Returns in (text, html) form
        """
        self.top_node = top_node
        html, text = '', ''

        self.remove_negativescores_nodes()

        if self.config.keep_article_html:
            html = self.convert_to_html()

        self.links_to_text()
        self.add_newline_to_br()
        self.add_newline_to_li()
        self.replace_with_text()
        self.remove_empty_tags()
        self.remove_trailing_media_div()
        text = self.convert_to_text(title)
        return (text, html)

    def convert_to_text(self, title=None):
        text_elements = filter(
            None,
            (self._extractor.extract_text(node, title=title) for node in self.get_top_node())
        )
        flattened_elements =  chain.from_iterable(text_elements)

        # If the first element looks like a single word, it's most likely not the first
        # sentence, but kind of a title article.
        first_element = next(flattened_elements, None)
        elements = ()
        if first_element and len(first_element.split()) != 1:
            elements = (first_element, )

        return '\n\n'.join(chain(elements, flattened_elements))

    def convert_to_html(self):
        cleaned_node = self.parser.clean_article_html(self.get_top_node())
        return self.parser.nodeToString(cleaned_node)

    def add_newline_to_br(self):
        for e in self.parser.getElementsByTag(self.top_node, tag='br'):
            e.text = r'\n'

    def add_newline_to_li(self):
        for e in self.parser.getElementsByTag(self.top_node, tag='ul'):
            li_list = self.parser.getElementsByTag(e, tag='li')
            for li in li_list[:-1]:
                li.text = self.parser.getText(li) + r'\n'
                for c in self.parser.getChildren(li):
                    self.parser.remove(c)

    def links_to_text(self):
        """Cleans up and converts any nodes that should be considered
        text into text.
        """
        self.parser.stripTags(self.get_top_node(), 'a')

    def remove_negativescores_nodes(self):
        """If there are elements inside our top node that have a
        negative gravity score, let's give em the boot.
        """
        gravity_items = self.parser.css_select(
            self.top_node, "*[gravityScore]")
        for item in gravity_items:
            score = self.parser.getAttribute(item, 'gravityScore')
            score = float(score) if score else 0
            if score < 1:
                item.getparent().remove(item)

    def replace_with_text(self):
        """
        Replace common tags with just text so we don't have any crazy
        formatting issues so replace <br>, <i>, <strong>, etc....
        With whatever text is inside them.
        code : http://lxml.de/api/lxml.etree-module.html#strip_tags
        """
        self.parser.stripTags(
            self.get_top_node(), 'b', 'strong', 'i', 'br', 'sup')

    def remove_empty_tags(self):
        """It's common in top_node to exit tags that are filled with data
        within properties but not within the tags themselves, delete them
        """
        all_nodes = self.parser.getElementsByTags(
            self.get_top_node(), ['*'])
        all_nodes.reverse()
        for el in all_nodes:
            tag = self.parser.getTag(el)
            text = self.parser.getText(el)
            if (tag != 'br' or text != '\\r') \
                    and not text \
                    and len(self.parser.getElementsByTag(
                        el, tag='object')) == 0 \
                    and len(self.parser.getElementsByTag(
                        el, tag='embed')) == 0:
                self.parser.remove(el)

    def remove_trailing_media_div(self):
        """Punish the *last top level* node in the top_node if it's
        DOM depth is too deep. Many media non-content links are
        eliminated: "related", "loading gallery", etc
        """

        def get_depth(node, depth=1):
            """Computes depth of an lxml element via BFS, this would be
            in parser if it were used anywhere else besides this method
            """
            children = self.parser.getChildren(node)
            if not children:
                return depth
            max_depth = 0
            for c in children:
                e_depth = get_depth(c, depth + 1)
                if e_depth > max_depth:
                    max_depth = e_depth
            return max_depth

        top_level_nodes = self.parser.getChildren(self.get_top_node())
        if len(top_level_nodes) < 3:
            return

        last_node = top_level_nodes[-1]
        if get_depth(last_node) >= 2:
            self.parser.remove(last_node)
