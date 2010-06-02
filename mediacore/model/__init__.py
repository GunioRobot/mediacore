# This file is a part of MediaCore, Copyright 2009 Simple Station Inc.
#
# MediaCore is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# MediaCore is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.
"""The application's model objects"""
from mediacore.model.meta import DBSession, Base

import re, unicodedata
import webob.exc
from sqlalchemy import sql, orm
from sqlalchemy.orm import class_mapper
from sqlalchemy.orm.exc import NoResultFound
from sqlalchemy.sql.expression import ColumnClause as _ColumnClause
from mediacore.lib.htmlsanitizer import entities_to_unicode
from mediacore.lib.unidecode import unidecode

# maximum length of slug strings for all objects.
slug_length = 50

#####
# Generally you will not want to define your table's mappers, and data objects
# here in __init__ but will want to create modules them in the model directory
# and import them at the bottom of this file.
######

def init_model(engine):
    """Call me before using any of the tables or classes in the model."""
    DBSession.configure(bind=engine)


def fetch_row(mapped_class, pk=None, extra_filter=None, **kwargs):
    """Fetch a single row from the database or else trigger a 404.

    Typical usage is to fetch a single row for display or editing::

        class PageController(object):
            @expose()
            def index(self, id):
                page = fetch_row(Page, id)
                return page.name

            @expose()
            def works_with_slugs_too(self, slug):
                page = fetch_row(Page, slug=slug)
                return page.name

    If the ``pk`` is string ``new`` then an empty instance of ``mapped_class``
    is created and returned. This is helpful in admin controllers where you
    may reuse your *edit* action for *adding* too.

    :param mapped_class: An ORM-controlled model
    :param pk: A particular primary key to filter by.
    :type pk: int, ``None`` or ``"new"``
    :param extra_filter: Extra filter arguments.
    :param \*\*kwargs: Any extra args are treated as column names to filter by.
        See :meth:`sqlalchemy.orm.Query.filter_by`.
    :returns: An instance of ``mapped_class``.
    :raises webob.exc.HTTPNotFound: If no result is found

    """
    if pk == 'new':
        inst = mapped_class()
        return inst

    query = DBSession.query(mapped_class)

    if pk is not None:
        mapper = class_mapper(mapped_class, compile=False)
        query = query.filter(mapper.primary_key[0] == pk)
    if kwargs:
        query = query.filter_by(**kwargs)
    if extra_filter is not None:
        query = query.filter(extra_filter)

    try:
        return query.one()
    except NoResultFound:
        raise webob.exc.HTTPNotFound


# slugify regex's
_whitespace = re.compile(r'\s+')
_non_alpha = re.compile(r'[^a-z0-9_-]')
_extra_dashes = re.compile(r'-+')

def slugify(string):
    """Produce a URL-friendly string from the input.

    XHTML entities are converted to unicode, and then replaced with the
    best-choice ascii equivalents.

    :param string: A title, name, etc
    :type string: unicode
    :returns: Ascii URL-friendly slug
    :rtype: string

    """
    string = unicodedata.normalize('NFKD', string).encode('ascii', 'ignore')
    string = unicode(string).lower()
    # Replace xhtml entities
    string = entities_to_unicode(string)
    # Transliterate to ASCII, as best as possible:
    string = unidecode(string)
    # String may now contain '[?]' triplets to describe unknown characters.
    # These will be stripped out by the following regexes.
    string = _whitespace.sub(u'-', string)
    string = _non_alpha.sub(u'', string)
    string = _extra_dashes.sub(u'-', string).strip('-')

    return string[:slug_length]

def get_available_slug(mapped_class, string, ignore=None):
    """Return a unique slug based on the provided string.

    Works by appending an int in sequence starting with 2:

        1. awesome-stuff
        2. awesome-stuff-2
        3. awesome-stuff-3

    :param mapped_class: The ORM-controlled model that the slug is for
    :param string: A title, name, etc
    :type string: unicode
    :param ignore: A record which doesn't count as a collision
    :type ignore: Int ID, ``mapped_class`` instance or None
    :returns: A unique slug
    :rtype: string
    """
    if isinstance(ignore, mapped_class):
        ignore = ignore.id
    elif ignore is not None:
        ignore = int(ignore)

    new_slug = slug = slugify(string)
    appendix = 2
    while DBSession.query(mapped_class.id)\
            .filter(mapped_class.slug == new_slug)\
            .filter(mapped_class.id != ignore)\
            .first():
        str_appendix = '-%s' % appendix
        max_substr_len = slug_length - len(str_appendix)
        new_slug = slug[:max_substr_len] + str_appendix
        appendix += 1

    return new_slug

def _properties_dict_from_labels(*args):
    """Produce a dictionary of mapper properties from the given args list.

    Intended to make the process of producing lots of column properties
    less verbose and painful.

    """
    properties_dict = {}
    for property in args:
        label = property.columns[0].name
        properties_dict[label] = property
    return properties_dict

def _mtm_count_property(label, assoc_table,
                        where=None, deferred=True, **kwargs):
    """Return a column property for fetching the comment count for some object.

    :param label:
      A descriptive label for the correlated subquery. Should probably be the
      same as the name of the property set on the mapper.

    :param assoc_table:
      The many-to-many table which associates the comments table to the parent
      table. We expect the primary key to be two columns, one a foreign key to
      comments, the other a foreign key to the parent table.

    :param where:
      Optional additional where clauses. If given a list, the elements are
      wrapped in an AND clause.

    :param deferred:
      By default the count will be fetched when first accessed. To prefetch
      during the initial query, use:
        DBSession.query(ParentObject).options(undefer('comment_count_xyz'))
    :type deferred: bool

    :param \**kwargs:
      Any additional arguments are passed to sqlalchemy.orm.column_property
    """
    where_clauses = []
    for assoc_column in assoc_table.primary_key:
        fk = assoc_column.foreign_keys[0]
        where_clauses.append(fk.column == assoc_column)
    if isinstance(where, list):
        where_clauses.extend(where)
    elif where is not None:
        where_clauses.append(where)

    subselect = sql.select(
        [sql.func.coalesce(sql.func.count(sql.text('*')), sql.text('0'))],
        sql.and_(*where_clauses),
    )
    if label is not None:
        subselect = subselect.label(label)

    return orm.column_property(subselect, deferred=deferred, **kwargs)

class _MatchAgainstClause(_ColumnClause):
    """
    A ColumnClause which allows for bindparams in a literal SQL column.

    For some reason :meth:`sqlalchemy.orm.session.Session.execute` does
    some extra processing to bindparams that
    :meth:`sqlalchemy.engine.base.Connection.execute` does not.
    Bind params are parsed and appear to be injected using either
    positional arguments or python string replacement(?). Ultimately,
    the query works on the connection, but session replaces bindparams
    with ``%s`` etc, and when a ``:param`` reaches the database as a
    plain string and without a value to go with it, an error is thrown.

    This extension tricks :mod:`sqlalchemy.sql.compiler` into parsing
    and rendering the bindparam properly. For some reason,
    :func:`sqlalchemy.sql.expression.literal_column` doesn't check
    for bind params, while :func:`sqlalchemy.sql.expression.text` does.
    See :meth:`sqlalchemy.sql.compiler.DefaultCompiler.visit_textclause`
    and :meth:`sqlalchemy.sql.compiler.DefaultCompiler.visit_column`.

    This solution is far from perfect but it does the trick for now.

    """

    __visit_name__ = 'textclause'
    bindparams = {}
    typemap = None

    def __init__(self, columns, param, bool=False, *args, **kwargs):
        if isinstance(columns, list):
            columns = ', '.join(str(c) for c in columns)
        if isinstance(param, basestring):
            param = sql.bindparam(param)
        bool_mode = bool and ' IN BOOLEAN MODE' or ''

        self.text = 'MATCH (%s) AGAINST (%s%s)' % (columns, param, bool_mode)
        self.bindparams[param.key] = param

        _ColumnClause.__init__(self, self.text, *args, **kwargs)


from mediacore.model.auth import User, Group, Permission
from mediacore.model.authors import Author, AuthorWithIP
from mediacore.model.comments import Comment
from mediacore.model.settings import Setting
from mediacore.model.tags import Tag
from mediacore.model.categories import Category
from mediacore.model.media import Media, MediaFile
from mediacore.model.podcasts import Podcast
