from contextlib import contextmanager
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker, declarative_base
from .config import settings

if settings.DATABASE_URL.startswith("sqlite"):
    # Increase timeout significantly for industrial environments
    # 'isolation_level' set to None allows manual control or use standard behavior
    engine = create_engine(
        settings.DATABASE_URL, 
        echo=settings.SQL_ECHO, 
        connect_args={
            "check_same_thread": False, 
            "timeout": 30
        }
    )
    
    # Enable WAL mode and other performance pragmas
    @event.listens_for(engine, "connect")
    def set_sqlite_pragma(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute("PRAGMA cache_size=-64000") # 64MB cache
        cursor.execute("PRAGMA temp_store=MEMORY")
        cursor.close()
else:
    engine = create_engine(settings.DATABASE_URL, echo=settings.SQL_ECHO)

SessionLocal = sessionmaker(
    autocommit=False, 
    autoflush=False, 
    bind=engine
)

Base = declarative_base()

@contextmanager
def get_session():
    """Robust session manager that handles commits and closing safely."""
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception as e:
        session.rollback()
        raise e
    finally:
        session.close()