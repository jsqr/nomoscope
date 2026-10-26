-- Views and macros over the dataset views that build.py creates first.
--
-- law_in_force combines a version's dates into the interval it was in force.
-- start_basis and end_basis name the column each end came from.

CREATE VIEW law_in_force AS
WITH started AS (
    SELECT v.*,
           coalesce(v.operative_date, v.effective_date, v.first_seen) AS start_date,
           CASE WHEN v.operative_date IS NOT NULL THEN 'operative'
                WHEN v.effective_date IS NOT NULL THEN 'effective'
                ELSE 'first_seen' END AS start_basis
    FROM law_versions v
)
SELECT a.id, a.section_id, a.jurisdiction, a.law_code, a.section_num, a.node_id,
       a.first_seen, a.last_seen, a.absent_from,
       a.start_date, a.start_basis,
       coalesce(a.repeal_date, a.inoperative_date, b.start_date, a.absent_from) AS end_date,
       CASE WHEN a.repeal_date IS NOT NULL THEN 'repeal'
            WHEN a.inoperative_date IS NOT NULL THEN 'inoperative'
            WHEN b.start_date IS NOT NULL THEN 'successor'
            WHEN a.absent_from IS NOT NULL THEN 'absent'
            ELSE 'current' END AS end_basis
FROM started a
LEFT JOIN started b ON b.jurisdiction = a.jurisdiction AND b.id = a.successor_id;

-- Where each version sat on a date: its latest placement first seen on or
-- before the date. A version first seen after the date gets its earliest
-- placement.
CREATE MACRO law_placed_at(in_jurisdiction, on_date) AS TABLE
SELECT id,
       coalesce(max_by(node_treepath, first_seen) FILTER (WHERE first_seen <= on_date),
                min_by(node_treepath, first_seen)) AS node_treepath,
       coalesce(max_by(node_id, first_seen) FILTER (WHERE first_seen <= on_date),
                min_by(node_id, first_seen)) AS node_id
FROM law_observations
WHERE jurisdiction = in_jurisdiction
GROUP BY id;

-- Every version in force on a date, with its text and its placement then.
CREATE MACRO law_at(in_jurisdiction, on_date) AS TABLE
SELECT f.* EXCLUDE (node_id), p.node_id, p.node_treepath, v.heading, v.history, v.text
FROM law_in_force f
JOIN law_versions v ON v.jurisdiction = f.jurisdiction AND v.id = f.id
JOIN law_placed_at(in_jurisdiction, on_date) p ON p.id = f.id
WHERE f.jurisdiction = in_jurisdiction
  AND f.start_date <= on_date
  AND (f.end_date IS NULL OR f.end_date > on_date);

-- A session's stated end date, else the date of its last publication.
CREATE MACRO session_end(in_jurisdiction, in_session) AS (
    coalesce(
        (SELECT end_date FROM sessions
         WHERE jurisdiction = in_jurisdiction AND session = in_session),
        (SELECT max(published_date) FROM publications
         WHERE jurisdiction = in_jurisdiction AND session = in_session)
    )
);

CREATE MACRO law_at_session(in_jurisdiction, in_session) AS TABLE
SELECT * FROM law_at(in_jurisdiction, session_end(in_jurisdiction, in_session));

-- One section in force on a date, or one of its subdivisions, with its
-- text. in_path is the subdivision's path ('b/1'); '' gives the section.
CREATE MACRO subdivision_at(in_jurisdiction, in_code, in_section, in_path, on_date) AS TABLE
SELECT l.jurisdiction, l.law_code, l.section_num, coalesce(s.path, '') AS path, s.level,
       l.heading, l.id AS law_version_id,
       CASE WHEN s.path IS NULL THEN l.text
            ELSE substr(l.text, s.start_char + 1, s.end_char - s.start_char) END AS text,
       l.start_date, l.start_basis, l.end_date, l.end_basis
FROM law_at(in_jurisdiction, on_date) l
LEFT JOIN law_subdivisions s
       ON s.law_version_id = l.id AND s.path = in_path AND in_path <> ''
WHERE l.law_code = in_code AND l.section_num = in_section
  AND (in_path = '' OR s.path IS NOT NULL);
