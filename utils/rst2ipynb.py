#!/usr/bin/env python
# emacs: -*- coding: utf-8; mode: python; py-indent-offset: 4; indent-tabs-mode: nil -*-
# vi: set ft=python sts=4 ts=4 sw=4 et:
### ### ### ### ### ### ### ### ### ### ### ### ###
#
#   See COPYING file distributed along with the PyMVPA package for the
#   copyright and license terms.
#
### ### ### ### ### ### ### ### ### ### ### ### ###
"""Helper to convert ReST Sphinx documents into IPython notebooks"""

__docformat__ = 'restructuredtext'

import sys
import os
import re
from docutils.core import publish_doctree
from doctest import script_from_examples, DocTestParser
import json
from copy import deepcopy
import optparse
import glob

def prep_rest(rest):
    # implementing above in python 2.6 compatible fashion
    # which lacks flags keyword argument for re.subn
    # We will first replace all newlines with a unique %%%
    # which we will use as a new line symbol in our expressions
    assert(not '%%%' in rest)           # at least some checking
    doc = rest.replace('\n', '%%%')
    doc, _ = re.subn(r'%%%.. index.*?%%%', '', doc)
    # fold text roles inside to turn them into a ref
    subns = 1
    while not subns == 0:
        doc, subns = re.subn(r':([a-z]+):`(.*)`', '`:\\1:\\2`', doc)
    doc = doc.replace('%%%', '\n')      # replace %%% back
    return doc


class ReST2IPyNB(object):
    ipynb_template = {
        "metadata": {"name": ""},
        "nbformat": 3,
        "nbformat_minor": 0,
        "worksheets": [{"cells": [], "metadata": {}}]
    }
    codecell_template = {
        "cell_type": "code",
        "collapsed": False,
        "input": [],
        "language": "python",
        "metadata": {},
        "outputs": []
    }
    markdowncell_template = {
        "cell_type": "markdown",
        "metadata": {},
        "source": []
    }
    headingcell_template = {
        "cell_type": "heading",
        "level": None,
        "metadata": {},
        "source": []
    }

    def __init__(self,
                 baseurl='',
                 apiref_baseurl='',
                 glossary_baseurl='',
                 doctree_parser_settings=None):
        self._doctree_parser_settings = None
        if doctree_parser_settings is None:
            # shut up
            self._doctree_parser_settings = {'report_level': 5}
        self._baseurl = baseurl
        self._apiref_baseurl = apiref_baseurl
        self._glossary_baseurl = glossary_baseurl
        self._doctest_parser = DocTestParser()
        self._reset_state()

    def _reset_state(self):
        self._state = {'sec_depth': 1, 'indent': 0, 'in_markdowncell': False,
                'need_new_codecell': False, 'need_hanging_indent': False}
        self._currcell = None
        self._buffer = ''
        self._notebook = None
        self._filename = None

    def __call__(self, filename):
        self._filename = filename
        rest = open(filename).read()
        doc = prep_rest(rest)
        self._notebook = deepcopy(ReST2IPyNB.ipynb_template)
        doctree = publish_doctree(
                    doc, settings_overrides=self._doctree_parser_settings)
        self._currcells = self._notebook['worksheets'][0]['cells']
        self._parse(doctree)
        self._store_currcell()
        notebook = self._notebook
        self._reset_state()
        return notebook

    def _ref2apiref(self, reftext):
        apiref_baseurl = self._apiref_baseurl
        # try to determine what kind of ref we got
        if reftext.startswith(':'):
            rtype, ref = re.match(':([a-z]+):(.*)', reftext).groups()
        else:
            rtype = None
            ref = reftext
        if rtype is None:
            # function?
            if ref.endswith('()'):
                rtype = 'func'
                ref = ref[:-2]
        refid = ref.lstrip('~').rstrip('()')
        if rtype == 'meth':
            ref_url = '%s/%s.html#%s' % (apiref_baseurl,
                                         '.'.join(refid.split('.')[:-1]),
                                         refid)
        else:
            ref_url = '%s/%s.html#%s' % (apiref_baseurl, refid, refid.replace('.', '-').replace('_', '-').lower())
        ref_label = None
        if ref.startswith('~'):
            if rtype == 'meth':
                ref_label = '%s()' % '.'.join(refid.split('.')[-2:])
            elif rtype == 'func':
                ref_label = '%s()' % refid.split('.')[-1]
            else:
                ref_label = '%s' % refid.split('.')[-1]
        return '[%s](%s)' % (ref_label, ref_url)

    def _parse(self, doctree):
        for child in doctree.children:
            tag = child.tagname
            if tag == 'title':
                self._add_headingcell(self._state['sec_depth'])
                self._parse(child)
                if not len(self._notebook['metadata']['name']):
                    self._notebook['metadata']['name'] = self._buffer
            elif tag == '#text':
                self._add2buffer(child.astext())
            elif tag == 'paragraph':
                self._add_markdowncell()
                if self._state['need_hanging_indent']:
                    self._state['need_hanging_indent'] = False
                else:
                    self._add2buffer('', newline=True, paragraph=True)
                self._flush_buffer()
                self._parse(child)
            # FIXME: literal_block likely needs better handling
            elif tag == 'literal_block':
                self._add_markdowncell()
                if self._state['need_hanging_indent']:
                    self._state['need_hanging_indent'] = False
                else:
                    self._add2buffer('', newline=True, paragraph=True)
                self._flush_buffer()
                self._parse(child)
            elif tag == 'inline':
                print("warning, no idea how to handle ``inline``")
                # FIXME:
            elif tag == 'raw':
                self._add_markdowncell()
                self._flush_buffer()
                self._currcell['source'].insert(0, child.astext())
                self._store_currcell()
            elif tag == 'doctest_block':
                self._add_codecell()
                needs_new_codecell = False
                for ex in self._doctest_parser.get_examples(child.rawsource):
                    if needs_new_codecell:
                        self._add_codecell()
                    self._add2buffer('%s%s' % (' ' * ex.indent, ex.source),
                                     newline=False)
                    self._flush_buffer(startnew=True)
                    needs_new_codecell = len(ex.want) > 0
            elif tag == 'section':
                self._state['sec_depth'] += 1
                self._parse(child)
                self._state['sec_depth'] -= 1
            elif tag == 'note':
                self._add_markdowncell(force=True)
                self._parse(child)
                self._flush_buffer()
                self._currcell['source'].insert(0, '- - -\n*Note*')
                self._currcell['source'].append('- - -\n')
                self._store_currcell()
            elif tag == 'title_reference':
                self._flush_buffer()
                self._parse(child)
                if self._buffer.startswith(':term:'):
                    # link to glossary
                    term = re.match('.*<(.*)>', self._buffer)
                    if term is None:
                        term = re.match(':term:(.*)', self._buffer).groups()[0]
                        term_text = term
                    else:
                        term = term.groups()[0]
                        term_text = re.match(':term:(.*) <', self._buffer).groups()[0]
                    self._buffer = '[%s](%s#term-%s)' % (term_text,
                                                 self._glossary_baseurl,
                                                 term.lower().replace(' ', '-'))
                elif self._buffer.startswith('~mvpa') \
                     or self._buffer.startswith('mvpa') \
                     or self._buffer.startswith(':meth:') \
                     or self._buffer.startswith(':mod:') \
                     or self._buffer.startswith(':class:') \
                     or self._buffer.startswith(':func:'):
                    # various API reference link variants
                    self._buffer = self._ref2apiref(self._buffer)
                # XXX for the rest I have no idea how to link them without huge
                # effort
                elif self._buffer.startswith(':ref:'):
                    self._buffer = '*%s*' \
                            % [m for m in re.match(':ref:(.*) <|:ref:(.*)',
                                                   self._buffer).groups()
                                                        if not m is None][0]
                elif self._buffer.startswith(':math:'):
                    self._buffer = '$$%s$$' % self._buffer
                elif re.match(':([a-z]+):', self._buffer):
                    # catch other ref type we should handle, but do not yet
                    raise RuntimeError("unhandled reference type '%s'" % self._buffer)
                else:
                    # plain refs seems to be mostly used for external API
                    self._buffer = '`%s`' % self._buffer
            elif tag == 'emphasis':
                self._flush_buffer()
                self._parse(child)
                self._buffer = '*%s*' % self._buffer
            elif tag == 'strong':
                self._flush_buffer()
                self._parse(child)
                self._buffer = '**%s**' % self._buffer
            elif tag == 'literal':
                # strip one layer of backticks
                self._add2buffer(child.rawsource[1:-1])
            elif tag == 'problematic':
                print('PROBLEMATIC: %s' % child)
                self._parse(child)
            elif tag == 'reference':
                self._flush_buffer()
                self._parse(child)
                self._buffer = '[%s][%s]' % (self._buffer,
                                             child.attributes['name'])
            elif tag in ['comment', 'target']:
                pass
            elif tag == 'definition_list':
                self._add_markdowncell()
                for item in child.children:
                    self._flush_buffer()
                    self._parse(item.children[0])
                    term = self._buffer
                    self._buffer = ''
                    self._parse(item.children[1])
                    self._buffer = '\n%s: %s' % (term, self._buffer)
            elif tag in ['enumerated_list', 'bullet_list']:
                self._add_markdowncell()
                for i, item in enumerate(child.children):
                    if tag == 'enumerated_list':
                        prefix = '%i.' % (i + 1,)
                    else:
                        prefix = '*'
                    self._flush_buffer()
                    self._add2buffer('%s ' % prefix,
                                     newline=True, paragraph=True)
                    self._state['indent'] += 4
                    self._state['need_hanging_indent'] = True
                    self._parse(item)
                    self._state['indent'] -= 4
                self._flush_buffer()
            elif tag == 'list_item':
                for c in child.children:
                    self._parse(c)
            elif tag == 'term':
                self._parse(child.children[0])
            elif tag == 'figure':
                # this can't be expressed in markdown
                self._flush_buffer()
                file_url = '%s/%s.html' % (self._baseurl,
                                           os.path.splitext(os.path.basename(self._filename))[0])
                self._add2buffer('\[Visit [%s](%s) to view this figure\]' % (file_url, file_url),
                                 newline=True, paragraph=True)
            elif tag == 'block_quote':
                self._flush_buffer()
                first_line = len(self._currcell['source'])
                # skip the wrapping paragraph
                self._parse(child.children[0])
                self._flush_buffer()
                self._currcell['source'][first_line] = \
                        '\n\n> %s' % self._currcell['source'][first_line]
            elif tag == 'system_message':
                if child.attributes['type'] == 'INFO':
                    pass
                elif child.children[0].astext() == 'Unknown directive type "exercise".':
                    exercise_text = \
                        '\n'.join([l.strip()
                            for l in child.children[1][0].astext().split('\n')][2:])

                    self._add_markdowncell(force=True)
                    self._parse(publish_doctree(
                                    exercise_text,
                                    settings_overrides=self._doctree_parser_settings))
                    self._flush_buffer()
                    self._currcell['source'].insert(0, '- - -\n*Exercise*')
                    self._add_codecell()
                    self._add2buffer('# you can use this cell to for this exercise\n')
                    self._add_markdowncell()
                    self._currcell['source'].append('- - -\n')
                elif child.children[0].astext() == 'Unknown directive type "todo".':
                    pass
                elif child.children[0].astext() == 'Unknown directive type "tikz".':
                    pass
                elif child.children[0].astext() == 'Unknown directive type "ipython".':
                    python_code = \
                        '\n'.join([l.strip()
                            for l in child.children[1][0].astext().split('\n')][2:])
                    self._flush_buffer()
                    self._add_codecell()
                    self._currcell['input'].insert(0, python_code)
                    self._store_currcell()
                else:
                    raise RuntimeError("cannot handle system message '%s'"
                                       % child.astext())
            else:
                if hasattr(child, 'line') and child.line:
                    line = ' on line %i' % child.line
                else:
                    line = ''
                raise RuntimeError("Unknown tag '%s'%s" % (tag, line))

    def _store_currcell(self):
        if not self._currcell is None:
            self._flush_buffer()
            if self._currcell['cell_type'] == 'code':
                # remove last newline to save on vertical space
                self._currcell['input'][-1] = self._currcell['input'][-1].rstrip('\n')
            self._currcells.append(self._currcell)
        self._currcell = None

    def _add_headingcell(self, level):
        self._store_currcell()
        self._currcell = deepcopy(ReST2IPyNB.headingcell_template)
        self._currcell['level'] = level

    def _add_codecell(self):
        self._store_currcell()
        self._currcell = deepcopy(ReST2IPyNB.codecell_template)

    def _add_markdowncell(self, force=False):
        if self._currcell is None \
           or not self._currcell['cell_type'] == 'markdown' \
           or force:
            # we need a new cell
            self._store_currcell()
            self._currcell = deepcopy(ReST2IPyNB.markdowncell_template)

    def _add2buffer(self, value, newline=False, paragraph=False):
        if paragraph:
            nl = '\n\n'
        else:
            nl = '\n'
        if newline:
            self._buffer += '%s%s%s' % (nl, ' ' * self._state['indent'], value)
        else:
            self._buffer += value

    def _flush_buffer(self, startnew=True):
        if not len(self._buffer):
            return
        if self._currcell['cell_type'] == 'code':
            target_field = 'input'
        else:
            target_field = 'source'
        if startnew or not len(self._currcell[target_field]):
            self._currcell[target_field].append(self._buffer)
        else:
            self._currcell[target_field][-1] += self._buffer
        self._buffer = ''


def main():
    from optparse import OptionParser
    parser = OptionParser( \
        usage="%prog [options] <filename|directory> [...]", \
        version="%prog 0.1", description="""
%prog converts Sphinx documentation pages in restructered text (ReST) format
into IPython notebooks.
""" )

    # define options
    parser.add_option('--verbose', action='store_true', dest='verbose',
                      default=False, help='print status messages')
    parser.add_option('-x', '--exclude', action='append', dest='excluded',
                      help="""\
Use this option to exclude single files from the to be parsed files. This is
especially useful to exclude files when parsing complete directories. This
option can be specified multiple times.
""")
    parser.add_option('-o', '--outdir', action='store', dest='outdir',
                      type='string', default=None, help="""\
Target directory to write the ReST output to. This is a required option.
""")
    parser.add_option('--baseurl', action='store', dest='baseurl',
                      type='string', default=None, help="""\
Base URL for references to the online HTML version of a file. Default=''
""")
    parser.add_option('--apiref_baseurl', action='store', dest='apiref_baseurl',
                      type='string', default=None, help="""\
Base URL for links to the online API reference. Default=''
""")
    parser.add_option('--glossary_baseurl', action='store', dest='glossary_baseurl',
                      type='string', default=None, help="""\
Base URL for links to the online glossary. Default=''
""")

    # parse options
    (opts, args) = parser.parse_args() # read sys.argv[1:] by default

    # check for required options
    if opts.outdir is None:
        print('Required option -o, --outdir not specified.')
        sys.exit(1)

    # build up list of things to parse
    toparse = []
    for t in args:
        # expand dirs
        if os.path.isdir(t):
            # add all python files in that dir
            toparse += glob.glob(os.path.join(t, '*.rst'))
        else:
            toparse.append(t)

    # filter parse list
    if not opts.excluded is None:
        toparse = [t for t in toparse if not t in opts.excluded]

    toparse_list = toparse
    toparse = set(toparse)

    if len(toparse) != len(toparse_list):
        print('Ignoring duplicate parse targets.')

    if not os.path.exists(opts.outdir):
        os.mkdir(opts.outdir)

    conv = ReST2IPyNB(baseurl=opts.baseurl,
                      apiref_baseurl=opts.apiref_baseurl,
                      glossary_baseurl=opts.glossary_baseurl)

    # finally process provided files
    for t in toparse:
        if opts.verbose:
            print("Processing '%s'" % t)
        notebook = conv(t)
        json_notebook = json.dumps(notebook, sort_keys=True, indent=2)
        open(os.path.join(
                opts.outdir,
                '%s.ipynb' % os.path.splitext(os.path.basename(t))[0]),
             'w').write(json_notebook)

if __name__ == '__main__':
    main()
