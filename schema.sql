BEGIN IMMEDIATE;

CREATE TABLE IF NOT EXISTS rule_revisions (
    rule_id TEXT NOT NULL,
    revision TEXT NOT NULL,
    point TEXT NOT NULL,
    threshold_hex TEXT NOT NULL,
    PRIMARY KEY (rule_id, revision)
);

CREATE TABLE IF NOT EXISTS recordings (
    recording TEXT PRIMARY KEY,
    cursor INTEGER NOT NULL DEFAULT 0,
    end_seq INTEGER,
    CHECK (cursor >= 0 AND (end_seq IS NULL OR end_seq = cursor))
);

CREATE TABLE IF NOT EXISTS lanes (
    recording TEXT NOT NULL REFERENCES recordings(recording),
    generation INTEGER NOT NULL,
    slot INTEGER NOT NULL CHECK (slot IN (0, 1)),
    rule_id TEXT NOT NULL,
    revision TEXT NOT NULL,
    active_after INTEGER NOT NULL,
    retired_through INTEGER,
    PRIMARY KEY (recording, generation),
    FOREIGN KEY (rule_id, revision) REFERENCES rule_revisions(rule_id, revision),
    CHECK (generation >= 0 AND active_after >= 0),
    CHECK (retired_through IS NULL OR retired_through >= active_after)
);
CREATE UNIQUE INDEX IF NOT EXISTS one_active_binding_per_slot
    ON lanes(recording, slot) WHERE retired_through IS NULL;

CREATE TABLE IF NOT EXISTS emissions (
    recording TEXT NOT NULL,
    generation INTEGER NOT NULL,
    delivery_no INTEGER NOT NULL CHECK (delivery_no > 0),
    source_seq INTEGER NOT NULL CHECK (source_seq > 0),
    point TEXT NOT NULL,
    temperature_hex TEXT NOT NULL,
    PRIMARY KEY (recording, generation, delivery_no),
    UNIQUE (recording, generation, source_seq),
    FOREIGN KEY (recording, generation) REFERENCES lanes(recording, generation)
);

CREATE TABLE IF NOT EXISTS device_seals (
    recording TEXT NOT NULL,
    generation INTEGER NOT NULL,
    cutoff INTEGER NOT NULL,
    last_delivery INTEGER NOT NULL CHECK (last_delivery >= 0),
    PRIMARY KEY (recording, generation, cutoff),
    FOREIGN KEY (recording, generation) REFERENCES lanes(recording, generation)
);

CREATE TABLE IF NOT EXISTS slot_actions (
    recording TEXT NOT NULL,
    operation_id TEXT NOT NULL,
    action TEXT NOT NULL CHECK (action IN ('enable', 'retire')),
    generation INTEGER NOT NULL,
    boundary INTEGER NOT NULL CHECK (boundary >= 0),
    PRIMARY KEY (recording, operation_id, action),
    FOREIGN KEY (recording, generation) REFERENCES lanes(recording, generation)
);

CREATE TABLE IF NOT EXISTS shifts (
    shift_id TEXT PRIMARY KEY,
    recording TEXT NOT NULL UNIQUE,
    initial_generation INTEGER NOT NULL,
    FOREIGN KEY (recording, initial_generation) REFERENCES lanes(recording, generation)
);

CREATE TABLE IF NOT EXISTS change_operations (
    recording TEXT NOT NULL REFERENCES shifts(recording),
    operation_id TEXT NOT NULL,
    position INTEGER NOT NULL CHECK (position > 0),
    old_generation INTEGER NOT NULL,
    new_slot INTEGER NOT NULL CHECK (new_slot IN (0, 1)),
    new_rule_id TEXT NOT NULL,
    new_revision TEXT NOT NULL,
    enabled_after INTEGER,
    retired_through INTEGER,
    boundary INTEGER,
    PRIMARY KEY (recording, operation_id),
    UNIQUE (recording, position),
    FOREIGN KEY (recording, old_generation) REFERENCES lanes(recording, generation),
    FOREIGN KEY (new_rule_id, new_revision) REFERENCES rule_revisions(rule_id, revision)
);
CREATE UNIQUE INDEX IF NOT EXISTS one_pending_change
    ON change_operations(recording) WHERE boundary IS NULL;

CREATE TABLE IF NOT EXISTS shift_closures (
    shift_id TEXT PRIMARY KEY REFERENCES shifts(shift_id),
    end_seq INTEGER NOT NULL CHECK (end_seq >= 0)
);

CREATE TABLE IF NOT EXISTS received_deliveries (
    recording TEXT NOT NULL,
    generation INTEGER NOT NULL,
    delivery_no INTEGER NOT NULL,
    source_seq INTEGER NOT NULL,
    point TEXT NOT NULL,
    temperature_hex TEXT NOT NULL,
    PRIMARY KEY (recording, generation, delivery_no),
    UNIQUE (recording, generation, source_seq),
    FOREIGN KEY (recording, generation) REFERENCES lanes(recording, generation)
);

CREATE TABLE IF NOT EXISTS received_seals (
    recording TEXT NOT NULL,
    generation INTEGER NOT NULL,
    cutoff INTEGER NOT NULL,
    last_delivery INTEGER NOT NULL,
    PRIMARY KEY (recording, generation, cutoff),
    FOREIGN KEY (recording, generation) REFERENCES lanes(recording, generation)
);

CREATE TABLE IF NOT EXISTS custody_pair (
    shift_id TEXT PRIMARY KEY REFERENCES shifts(shift_id),
    role TEXT NOT NULL CHECK (role IN ('device', 'station')),
    device_id TEXT NOT NULL CHECK (length(device_id) > 0),
    station_id TEXT NOT NULL CHECK (length(station_id) > 0),
    key BLOB NOT NULL CHECK (length(key) > 0),
    recording TEXT NOT NULL,
    generation INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS custody_delivery_history (
    shift_id TEXT NOT NULL REFERENCES shifts(shift_id),
    recording TEXT NOT NULL,
    generation INTEGER NOT NULL,
    delivery_no INTEGER NOT NULL CHECK (delivery_no > 0),
    source_seq INTEGER NOT NULL CHECK (source_seq > 0),
    PRIMARY KEY (recording, generation, delivery_no),
    UNIQUE (recording, generation, source_seq),
    FOREIGN KEY (recording, generation) REFERENCES lanes(recording, generation)
);

CREATE TABLE IF NOT EXISTS reports (
    report_id TEXT PRIMARY KEY,
    shift_id TEXT NOT NULL REFERENCES shifts(shift_id),
    recording TEXT NOT NULL,
    end_seq INTEGER NOT NULL,
    payload TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS publication_receipts (
    serial INTEGER PRIMARY KEY AUTOINCREMENT,
    report_id TEXT NOT NULL UNIQUE REFERENCES reports(report_id)
);

COMMIT;
