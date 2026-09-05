PRAGMA application_id = 1129072728;
PRAGMA user_version = 1;
PRAGMA foreign_keys = ON;
PRAGMA journal_mode = DELETE;
PRAGMA synchronous = FULL;
PRAGMA temp_store = MEMORY;
PRAGMA page_size = 4096;

CREATE TABLE metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
) STRICT;

CREATE TABLE dataset (
    dataset_id TEXT PRIMARY KEY,
    file_name TEXT NOT NULL,
    source_url TEXT NOT NULL,
    retrieved_at TEXT NOT NULL,
    published_at TEXT,
    sha256 TEXT NOT NULL,
    byte_count INTEGER NOT NULL,
    media_type TEXT NOT NULL,
    license_id TEXT NOT NULL,
    tier TEXT NOT NULL,
    etag TEXT,
    last_modified TEXT,
    version_id TEXT,
    limitations_json TEXT NOT NULL,
    warnings_json TEXT NOT NULL,
    record_count INTEGER NOT NULL DEFAULT 0
) STRICT;

CREATE TABLE source_archive (
    dataset_id TEXT PRIMARY KEY REFERENCES dataset(dataset_id),
    media_type TEXT NOT NULL,
    byte_count INTEGER NOT NULL,
    sha256 TEXT NOT NULL,
    raw BLOB NOT NULL
) STRICT;

CREATE TABLE source_member (
    dataset_id TEXT NOT NULL REFERENCES dataset(dataset_id),
    path TEXT NOT NULL,
    media_type TEXT NOT NULL,
    byte_count INTEGER NOT NULL,
    compressed_bytes INTEGER NOT NULL,
    is_directory INTEGER NOT NULL CHECK (is_directory IN (0, 1)),
    sha256 TEXT NOT NULL,
    raw BLOB NOT NULL,
    parser_status TEXT NOT NULL,
    limitation TEXT,
    headers_json TEXT,
    record_count INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (dataset_id, path)
) STRICT;

CREATE TABLE record (
    record_pk INTEGER PRIMARY KEY,
    record_id TEXT NOT NULL UNIQUE,
    dataset_id TEXT NOT NULL,
    member TEXT NOT NULL,
    ordinal INTEGER NOT NULL,
    json_pointer TEXT,
    parent_pointer TEXT,
    fields_json TEXT NOT NULL,
    validated_id TEXT,
    FOREIGN KEY (dataset_id, member) REFERENCES source_member(dataset_id, path),
    UNIQUE (dataset_id, member, ordinal, json_pointer)
) STRICT;

CREATE TABLE membership (
    record_pk INTEGER NOT NULL REFERENCES record(record_pk),
    kind TEXT NOT NULL,
    value TEXT NOT NULL,
    match_mode TEXT NOT NULL,
    source_field TEXT NOT NULL,
    tokenizer TEXT,
    PRIMARY KEY (record_pk, kind, value, match_mode, source_field)
) STRICT;

CREATE VIRTUAL TABLE record_fts USING fts5(
    search_text,
    content = '',
    tokenize = 'unicode61 remove_diacritics 2'
);

CREATE INDEX record_dataset_member_ordinal
    ON record(dataset_id, member, ordinal, json_pointer);
CREATE INDEX record_parent_pointer
    ON record(dataset_id, member, parent_pointer);
CREATE INDEX membership_lookup
    ON membership(kind, value, match_mode, record_pk);
