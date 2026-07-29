ALTER TABLE account_recommendations
    ADD COLUMN watch_status TEXT
        CHECK (watch_status IS NULL OR watch_status IN ('current', 'completed'));

ALTER TABLE account_recommendations
    ADD COLUMN watched_episodes INTEGER
        CHECK (watched_episodes IS NULL OR watched_episodes >= 1);

UPDATE account_recommendations
   SET watch_status = 'completed'
 WHERE feedback_state = 'watched';
