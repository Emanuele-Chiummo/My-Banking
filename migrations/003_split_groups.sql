-- Migration 003: gruppi split P2P

CREATE TABLE IF NOT EXISTS split_groups (
  group_id   TEXT PRIMARY KEY,
  user_id    TEXT NOT NULL,
  name       TEXT NOT NULL,
  created_at TEXT DEFAULT (datetime('now')),
  FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS split_group_members (
  member_id    TEXT PRIMARY KEY,
  group_id     TEXT NOT NULL,
  contact_id   TEXT NOT NULL,
  display_name TEXT NOT NULL,
  created_at   TEXT DEFAULT (datetime('now')),
  FOREIGN KEY (group_id) REFERENCES split_groups(group_id) ON DELETE CASCADE,
  FOREIGN KEY (contact_id) REFERENCES contacts(contact_id) ON DELETE CASCADE,
  UNIQUE (group_id, contact_id)
);

CREATE INDEX IF NOT EXISTS idx_split_groups_user ON split_groups(user_id);
CREATE INDEX IF NOT EXISTS idx_split_members_group ON split_group_members(group_id);
