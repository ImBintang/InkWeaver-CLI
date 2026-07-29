"""建表语句、索引定义、迁移 SQL"""

# ── 类别表 ──
CATEGORIES_TABLE = """
CREATE TABLE IF NOT EXISTS categories (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT UNIQUE NOT NULL,
    type        TEXT NOT NULL DEFAULT 'wiki',
    spec        TEXT NOT NULL DEFAULT '{}',
    created_at  INTEGER NOT NULL,
    updated_at  INTEGER NOT NULL
);
"""

# ── Wiki 主表 ──
WIKI_MAIN_TABLE = """
CREATE TABLE IF NOT EXISTS wiki_main (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL,
    category_id     INTEGER NOT NULL REFERENCES categories(id),
    current_version INTEGER NOT NULL,
    created_chapter INTEGER NOT NULL,
    updated_chapter INTEGER NOT NULL,
    created_at      INTEGER NOT NULL,
    updated_at      INTEGER NOT NULL
);
"""

# ── Wiki 索引表（版本表）──
WIKI_INDEX_TABLE = """
CREATE TABLE IF NOT EXISTS wiki_index (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    main_id     INTEGER NOT NULL REFERENCES wiki_main(id),
    chapter     INTEGER NOT NULL,
    keywords    TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '',
    state       TEXT NOT NULL DEFAULT '',
    tags        TEXT NOT NULL DEFAULT '[]',
    content     TEXT NOT NULL DEFAULT '',
    relations   TEXT NOT NULL DEFAULT '[]',
    created_at  INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_wiki_index_main ON wiki_index(main_id);
CREATE INDEX IF NOT EXISTS idx_wiki_index_chapter ON wiki_index(chapter);
"""

# ── Plot 主表 ──
PLOT_MAIN_TABLE = """
CREATE TABLE IF NOT EXISTS plot_main (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL UNIQUE,
    current_version INTEGER NOT NULL,
    chapters        TEXT NOT NULL DEFAULT '',
    ended           INTEGER NOT NULL DEFAULT 0,
    end_notes       TEXT NOT NULL DEFAULT '',
    created_chapter INTEGER NOT NULL,
    updated_chapter INTEGER NOT NULL,
    created_at      INTEGER NOT NULL,
    updated_at      INTEGER NOT NULL
);
"""

# ── Plot 索引表 ──
PLOT_INDEX_TABLE = """
CREATE TABLE IF NOT EXISTS plot_index (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    main_id     INTEGER NOT NULL REFERENCES plot_main(id),
    chapter     INTEGER NOT NULL,
    keywords    TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '',
    state       TEXT NOT NULL DEFAULT '',
    tags        TEXT NOT NULL DEFAULT '[]',
    content     TEXT NOT NULL DEFAULT '',
    relations   TEXT NOT NULL DEFAULT '[]',
    created_at  INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_plot_index_main ON plot_index(main_id);
"""

# ── Rules 主表 ──
RULES_MAIN_TABLE = """
CREATE TABLE IF NOT EXISTS rules_main (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL UNIQUE,
    current_version INTEGER NOT NULL,
    created_chapter INTEGER NOT NULL,
    updated_chapter INTEGER NOT NULL,
    created_at      INTEGER NOT NULL,
    updated_at      INTEGER NOT NULL
);
"""

# ── Rules 索引表 ──
RULES_INDEX_TABLE = """
CREATE TABLE IF NOT EXISTS rules_index (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    main_id     INTEGER NOT NULL REFERENCES rules_main(id),
    chapter     INTEGER NOT NULL,
    keywords    TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '',
    state       TEXT NOT NULL DEFAULT '',
    tags        TEXT NOT NULL DEFAULT '[]',
    content     TEXT NOT NULL DEFAULT '',
    relations   TEXT NOT NULL DEFAULT '[]',
    created_at  INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_rules_index_main ON rules_index(main_id);
"""

# ── Chapters 表（章节入库）──
CHAPTERS_TABLE = """
CREATE TABLE IF NOT EXISTS chapters (
    chapter_num  INTEGER PRIMARY KEY,
    title        TEXT NOT NULL DEFAULT '',
    content      TEXT NOT NULL DEFAULT '',
    imported_at  INTEGER NOT NULL
);
"""

# ── Memories 表（v5.3 记忆系统）──
MEMORIES_TABLE = """
CREATE TABLE IF NOT EXISTS memories (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    category    TEXT NOT NULL,
    content     TEXT NOT NULL,
    source      TEXT,
    chapter     INTEGER,
    created_at  INTEGER NOT NULL,
    updated_at  INTEGER NOT NULL,
    is_active   INTEGER DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_memories_category ON memories(category);
CREATE INDEX IF NOT EXISTS idx_memories_active ON memories(is_active);
"""

# ── Drafts 表（v6.0 草稿系统）──
DRAFTS_TABLE = """
CREATE TABLE IF NOT EXISTS drafts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    chapter_num INTEGER NOT NULL,
    title       TEXT DEFAULT '',
    content     TEXT NOT NULL,
    source      TEXT NOT NULL DEFAULT 'user',
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    word_count  INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_drafts_chapter ON drafts(chapter_num);
"""

ALL_TABLES = [
    CATEGORIES_TABLE,
    WIKI_MAIN_TABLE, WIKI_INDEX_TABLE,
    PLOT_MAIN_TABLE, PLOT_INDEX_TABLE,
    RULES_MAIN_TABLE, RULES_INDEX_TABLE,
    CHAPTERS_TABLE,
    MEMORIES_TABLE,
    DRAFTS_TABLE,
]

# ── Token 统计表（全局独立库 .env/token_stats.db）──
TOKEN_STATS_TABLE = """
CREATE TABLE IF NOT EXISTS token_traces (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    book          TEXT NOT NULL,
    agent         TEXT NOT NULL,
    model_id      TEXT,
    model_name    TEXT,
    input_tokens  INTEGER DEFAULT 0,
    output_tokens INTEGER DEFAULT 0,
    total_tokens  INTEGER DEFAULT 0,
    purpose       TEXT DEFAULT '',
    created_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_traces_book ON token_traces(book);
CREATE INDEX IF NOT EXISTS idx_traces_time ON token_traces(created_at);
"""
