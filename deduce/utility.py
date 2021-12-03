""" This module contains all kinds of utility functionality """

import codecs
import os
import re
import unicodedata
from functools import reduce

from deduce.utilcls import Token, TokenGroup, AbstractSpan


class Annotation:
    def __init__(self, start_ix: int, end_ix: int, tag: str, text: str):
        self.start_ix = start_ix
        self.end_ix = end_ix
        self.tag = tag
        self.text_ = text

    def __eq__(self, other):
        return (
            isinstance(other, Annotation)
            and self.start_ix == other.start_ix
            and self.end_ix == other.end_ix
            and self.tag == other.tag
            and self.text_ == other.text_
        )

    def __repr__(self):
        return self.tag + "[" + str(self.start_ix) + ":" + str(self.end_ix) + "]"


def merge_triebased(tokens: list[str], trie) -> list[str]:
    """
    This function merges all sublists of tokens that occur in the trie to one element
    in the list of tokens. For example: if the tree contains ["A", "1"],
    then in the list of tokens ["Patient", "is", "opgenomen", "op", "A", "1"]  the sublist
    ["A", "1"] can be found in the Trie and will thus be merged,
    resulting in ["Patient", "is", "opgenomen", "op", "A1"]
    """

    # Return this list
    tokens_merged = []
    i = 0

    # Iterate over tokens
    while i < len(tokens):

        # Check for each item until the end if there are prefixes of the list in the Trie
        prefix_matches = trie.find_all_prefixes(tokens[i:])

        # If no prefixes are in the Trie, append the first token and move to the next one
        if len(prefix_matches) == 0:
            tokens_merged.append(tokens[i])
            i += 1

        # Else check the maximum length list of tokens, append it to the list that will be returned,
        # and then skip all the tokens in the list
        else:
            max_list = max(prefix_matches, key=len)
            tokens_merged.append("".join(max_list))
            i += len(max_list)

    # Return the list
    return tokens_merged


def type_of(char):
    """Determines whether a character is alpha, a fish hook, or other"""

    if char.isalpha():
        return "alpha"

    if char in ("<", ">"):
        return "hook"

    return "other"


def any_in_text(matchlist, token):
    """Check if any of the strings in matchlist are in the string token"""
    return reduce(lambda x, y: x | y, map(lambda x: x in token, matchlist))


def context(tokens: list[Token], i):
    """Determine next and previous tokens that start with an alpha character"""

    # Find the next token
    k = i + 1
    next_token = None

    # Iterate over tokens after this one
    while k < len(tokens):

        # If any of these are found, no next token can be returned
        if tokens[k].text[0] == ")" or any_in_text(["\n", "\r", "\t"], tokens[k].text):
            next_token = None
            break

        # Else, this is the next token
        if tokens[k].text[0].isalpha() or tokens[k].is_annotation():
            next_token = tokens[k]
            break

        # If no token is found at this position, check the next
        k += 1

    # Index of the next token is simply the last checked position
    next_token_index = k

    # Find the previous token in a similar way
    k = i - 1
    previous_token = None

    # Iterate over all previous tokens
    while k >= 0:

        if tokens[k].text[0] == "(" or any_in_text(["\n", "\r", "\t"], tokens[k].text):
            previous_token = None
            break

        if tokens[k].text[0].isalpha() or tokens[k].is_annotation():
            previous_token = tokens[k]
            break

        k -= 1

    previous_token_index = k

    # Return the appropriate information in a 4-tuple
    return previous_token, previous_token_index, next_token, next_token_index


def is_initial(token):
    """
    Check if a token is an initial
    This is defined as:
        - Length 1 and capital
        - Already annotated initial
    """
    return (token.is_annotation() and 'INITI' in token.get_full_annotation()) or \
           (not token.is_annotation() and len(token.text) == 1 and token.text[0].isupper())


def flatten_text_all_phi(text: str) -> str:
    """
    This is inspired by flatten_text, but works for all PHI categories
    :param text: the text in which you wish to flatten nested annotations
    :return: the text with nested annotations replaced by a single annotation with the outermost category
    """
    to_flatten = find_tags(text)
    to_flatten.sort(key=lambda x: -len(x))

    for tag in to_flatten:
        _, value = flatten(tag)
        outermost_category = parse_tag(tag)[0]
        text = text.replace(tag, f"<{outermost_category} {value.strip()}>")

    return text

# TODO: re-use deduce.merge_adjacent_tags in this method
def flatten_text(tokens: list[AbstractSpan]) -> list[AbstractSpan]:
    """
    Flattens nested tags; e.g. tags like <INITIAL A <NAME Surname>>
    are flattened to <INITIALNAME A Surname>. This function only works for text wich
    has annotated person names, and not for other PHI categories!
    :param tokens: the list of tokens containing the annotations that need to be flattened
    :return: a new list of tokens containing only non-nested annotations
    """
    flattened = [token.flatten(with_annotation='PATIENT' if 'PAT' in token.get_full_annotation() else 'PERSOON')
                 for token in tokens]

    # Make sure adjacent tags are joined together (like <INITIAL A><PATIENT Surname>),
    # optionally with a whitespace, period, hyphen or comma between them.
    # This works because all adjacent tags concern names
    # (remember that the function flatten_text() can only be used for names)!
    end_ann_ix = None
    for i in range(len(flattened)-1, -1, -1):
        token = flattened[i]
        if not token.is_annotation():
            continue
        if end_ann_ix is None:
            end_ann_ix = i
            continue
        start_ann_ix = i
        joined_span = to_text(flattened[start_ann_ix:end_ann_ix+1])
        if re.fullmatch("<([A-Z]+)\s([\w.\s,]+)>([.\s\-,]+)[.\s]*<([A-Z]+)\s([\w.\s,]+)>", joined_span):
            group = TokenGroup([t.without_annotation() for t in flattened[start_ann_ix:end_ann_ix]],
                               token.annotation + flattened[end_ann_ix].annotation)
            tail = flattened[end_ann_ix + 1:] if end_ann_ix < len(flattened-1) else []
            flattened = flattened[:i] + [group] + tail
        end_ann_ix = start_ann_ix

    # Find all names of tags, to replace them with either "PATIENT" or "PERSOON"
    replaced = [token.with_annotation('PATIENT' if 'PATIENT' in token.get_full_annotation() else 'PERSOON')
                if token.is_annotation()
                else token
                for token in flattened]

    # Return the text with all replacements
    return replaced


def flatten(tag):

    """
    Recursively flattens one tag to a tuple of name and value using splitTag() method.
    For example, the tag <INITIAL A <NAME Surname>> will be returned (INITIALNAME, A Surname)
    Returns a tuple (name, value).
    """

    # Base case, where no fishhooks are present
    if "<" not in tag:
        return "", tag

    # Remove fishhooks from tag
    tag = tag[1:-1]

    # Split on whitespaces
    tagspl = tag.split(" ", 1)

    # Split on the first whitespace, so we can distinguish between name and rest
    tagname = tagspl[0]
    tagrest = tagspl[1]

    # Output is initially empty
    tagvalue = ""

    # Recurse on the rest of the tag
    for tag_part in split_tags(tagrest):

        # Flatten for each value in tagrest
        flattened_tagname, flattened_tagvalue = flatten(tag_part)

        # Simply append to tagnames and values
        tagname += flattened_tagname
        tagvalue += flattened_tagvalue

    # Return pair
    return tagname, tagvalue


def find_tags(text):
    """Finds and returns a list of all tags in a piece of text"""

    # Helper variables
    nest_depth = 0
    startpos = 0

    # Return this list
    toflatten = []

    # Iterate over all characters
    for index, _ in enumerate(text):

        # If an opening hook is encountered
        if text[index] == "<":

            # If the tag is not nested, new startposition
            if nest_depth == 0:
                startpos = index

            # Increase nest_depth
            nest_depth += 1

        # If an closing hook is encountered
        if text[index] == ">":

            # Always decrease nest_depth
            nest_depth -= 1

            # If the tag was not nested, add the tag to the return list
            if nest_depth == 0:
                toflatten.append(text[startpos : index + 1])

    # Return list
    return toflatten


def split_tags(text):
    """
    Splits a text on normal text and tags, for example "This is text with a <NAME name> in it"
    will     return: ["This is text with a ", "<NAME name>", " in it"]. Nested tags will be
    regarded as one tag.  This function can be used on text as a whole,
    but is more appropriately used in the value part of nested tags
    """

    # Helper variables
    nest_depth = 0
    startpos = 0

    # Return this list
    splitbytags = []

    # Iterate over all characters
    for index, _ in enumerate(text):

        # If an opening hook is encountered
        if text[index] == "<":

            # Split if the tag is not nested
            if nest_depth == 0:
                splitbytags.append(text[startpos:index])
                startpos = index

            # Increase the nest_depth
            nest_depth += 1

        # If a closing hook is encountered
        if text[index] == ">":

            # First decrease the nest_depth
            nest_depth -= 1

            # Split if the tag was not nested
            if nest_depth == 0:
                splitbytags.append(text[startpos : index + 1])
                startpos = index + 1

    # Append the last characters
    splitbytags.append(text[startpos:])

    # Filter empty elements in the list (happens for example when <tag><tag> occurs)
    return [x for x in splitbytags if len(x) > 0]


def get_data(path):
    """Define where to find the data files"""
    return os.path.join(os.path.abspath(os.path.dirname(__file__)), "data", path)


def _normalize_value(line):
    """Removes all non-ascii characters from a string"""
    line = str(bytes(line, encoding="ascii", errors="ignore"), encoding="ascii")
    return unicodedata.normalize("NFKD", line)


def read_list(
    list_name,
    encoding="utf-8",
    lower=False,
    strip=True,
    min_len=None,
    normalize=None,
    unique=True,
):
    """Read a list from file and return the values."""

    data = codecs.open(get_data(list_name), encoding=encoding)

    if normalize == "ascii":
        data = [_normalize_value(line) for line in data]

    if lower:
        data = [line.lower() for line in data]

    if strip:
        data = [line.strip() for line in data]

    if min_len:
        data = [line for line in data if len(line) >= min_len]

    if unique:
        data_nodoubles = list(set(data))
    else:
        return data

    return data_nodoubles


def parse_tag(tag: str) -> tuple:
    """
    Parse a Deduce-style tag into its tag proper and its text. Does not handle nested tags
    :param tag: the Deduce-style tag, for example, <VOORNAAMONBEKEND Peter>
    :return: the tag type and text, for example, ("VOORNAAMONBEKEND", "Peter")
    """
    split_ix = tag.index(" ")
    return tag[1:split_ix], tag[split_ix + 1 : len(tag) - 1]


def get_annotations(annotated_text: str, tags: list, n_leading_whitespaces=0) -> list:
    """
    Find structured annotations from tags, with indices pointing to the original text. ***Does not handle nested tags***
    :param annotated_text: the annotated text
    :param tags: the tags found in the text, listed in the order they appear in the text
    :param n_leading_whitespaces: the number of leading whitespaces in the raw text
    :return: the annotations with indices corresponding to the original (raw) text;
    this accounts for string stripping during annotation
    """
    ix = 0
    annotations = []
    raw_text_ix = n_leading_whitespaces
    for tag in tags:
        tag_ix = annotated_text.index(tag, ix) - ix
        tag_type, tag_text = parse_tag(tag)
        annotations.append(
            Annotation(
                raw_text_ix + tag_ix,
                raw_text_ix + tag_ix + len(tag_text),
                tag_type,
                tag_text,
            )
        )
        ix += tag_ix + len(tag)
        raw_text_ix += tag_ix + len(tag_text)
    return annotations


def get_first_non_whitespace(text: str) -> int:
    return text.index(text.lstrip()[0])

def to_text(tokens: list[AbstractSpan]) -> str:
    return ''.join([token.as_text() for token in tokens])
