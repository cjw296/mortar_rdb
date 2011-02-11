from logging import getLogger
from sqlalchemy import create_engine as sa_create_engine
from sqlalchemy import MetaData, Table, exceptions
from sqlalchemy.engine.reflection import Inspector
from sqlalchemy.ext.declarative import declarative_base as sa_declarative_base
from sqlalchemy.orm import scoped_session
from sqlalchemy.orm import sessionmaker
from sqlalchemy.schema import (
    MetaData,
    Table,
    DropTable,
    ForeignKeyConstraint,
    DropConstraint,
    )
from zope.component import getSiteManager
from zope.sqlalchemy import ZopeTransactionExtension
from zope.sqlalchemy.datamanager import STATUS_CHANGED

from .controlled import validate
from .interfaces import ISession

logger = getLogger('glc.db')

def registerSession(url=None,
                    name=u'',
                    engine=None,
                    echo=False,
                    transaction=True,
                    threaded=True,
                    config=None):
    """
    Create a :class:`~sqlalchemy.orm.session.Session` class and
    register it for later use.

    Generally, you'll only need to pass in a :mod:`SQLAlchemy`
    connection URL. If you want to register multiple sessions for a
    particular application, then you should name them.
    If you want to provide specific engine configuration, then you can
    pass in an :class:`~sqlalchemy.engine.base.Engine` instance.
    In that case, you must not pass in a URL.

    :param echo: If `True`, then all SQL will be echoed to the python
      logging framework. This option cannot be specified if you pass in
      an engine.

    :param threaded: If `True`, then :func:`getSession` will return a distinct
      session for each thread that it is called from but, within that thread,
      it will always return the same session. If it is `False`, every call
      to :func:`getSession` will return a new session.
    
    :param transaction:

      If `True`, a :mod:`SQLAlchemy` extension will
      be used that that enables the :mod:`transaction` package to
      manage the lifecycle of the SQLAlchemy session (eg:
      :meth:`~sqlalchemy.orm.session.Session.begin`/:meth:`~sqlalchemy.orm.session.Session.commit`/:meth:`~sqlalchemy.orm.session.Session.rollback`).
      This can only be done when threading is enabled.

      If `False`, you will need to make sure you call
      :meth:`~sqlalchemy.orm.session.Session.begin`/:meth:`~sqlalchemy.orm.session.Session.commit`/:meth:`~sqlalchemy.orm.session.Session.rollback`,
      as appropriate, yourself. 

    :param config: This is an options parameter that should be a
      :class:`~glc.db.controlled.Config` instance. Any config passed
      will be used to verify that the schema expected by the software
      matches that in the database. If it does not, an exception will be
      raised.
    """
    if (engine and url) or not (engine or url):
        raise TypeError('Must specify engine or url, but not both')

    if transaction and not threaded:
        raise TypeError(
            'Transactions can only be managed in multi-threaded code'
            )
        
    if engine:
        if echo:
            raise TypeError('Cannot specify echo if an engine is passed')
    else:
        engine = create_engine(url,echo=echo)

    if config is not None:
        validate(engine,config)

    logger.info('Registering session for %r with name %r',
                str(engine.url),
                name)

    params = dict(
            bind = engine,
            autoflush=True,
            autocommit=False,
            )
        
    if transaction:
        params['extension']=ZopeTransactionExtension(
            # We want transactions committed regardless of
            # whether or not we use the ORM.
            initial_state=STATUS_CHANGED,
            )
        if engine.dialect.name in ('postgresql', 'mysql'):
            params['twophase']=True
        
    Session = sessionmaker(**params)
    
    if threaded:
        Session = scoped_session(Session)
    
    getSiteManager().registerUtility(
        Session,
        provided=ISession,
        name=name,
        ) 

def create_engine(url,**params):
    """
    This returns an :class:`~sqlalchemy.engine.base.Engine`
    for the supplied url and keyword parameters.

    Default parameters for `encoding` and `pool_recycle` are added if
    a MySQL url is supplied.
    """
    if 'mysql' in url:
        # passed in should override
        for name,value in (('encoding','utf-8'),('pool_recycle',3600)):
            params[name]=params.get(name,value)
    return sa_create_engine(url,**params)

def drop_tables(engine):
    """
    Drop all the tables in the database attached to by the supplied
    engine.
    
    As many foreign key constraints as possible will be dropped
    first making this quite brutal!
    """
    # from http://www.sqlalchemy.org/trac/wiki/UsageRecipes/DropEverything
    conn = engine.connect()

    inspector = Inspector.from_engine(engine)

    # gather all data first before dropping anything.
    # some DBs lock after things have been dropped in 
    # a transaction.
    metadata = MetaData()

    tbs = []
    for table_name in inspector.get_table_names():
        fks = []
        for fk in inspector.get_foreign_keys(table_name):
            if not fk['name']:
                continue
            fks.append(
                ForeignKeyConstraint((),(),name=fk['name'])
                )
        t = Table(table_name,metadata,*fks)
        tbs.append(t)
        for fkc in fks:
            conn.execute(DropConstraint(fkc,cascade=True))

    for table in tbs:
        conn.execute(DropTable(table))

def getSession(name=u''):
    """
    Return a :class:`~sqlalchemy.orm.session.Session` instance from
    the current registry as registered with the supplied `name`.
    """
    return getSiteManager().getUtility(ISession,name)()

_bases = {}

def declarative_base(**kw):
    """
    Return a :obj:`Base` as would be returned by
    :func:`~sqlalchemy.ext.declarative.declarative_base`.

    Only one :obj:`Base` will exist for each combination of parameters
    that this function is called with. If it is called with the same
    combination of parameters more than once, subsequent calls will
    return the existing :obj:`Base`.

    This method should be used so that even if more than one package
    used by a project defines models, they will all end up in the
    same :class:`~sqlalchemy.schema.MetaData` instance and all have the
    same declarative registry.
    """
    key = tuple(kw.items())
    if key in _bases:
        return _bases[key]
    base = sa_declarative_base(**kw)
    _bases[key]=base
    return base