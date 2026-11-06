CREATE TABLE platform_metadata (
    name text PRIMARY KEY,
    value text NOT NULL
);
INSERT INTO platform_metadata(name,value) VALUES ('profile','local-single-user');
