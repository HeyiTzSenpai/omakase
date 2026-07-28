CREATE TABLE account_invitation_acceptances (
    public_number INTEGER PRIMARY KEY CHECK (public_number >= 1),
    invite_id INTEGER NOT NULL UNIQUE
        REFERENCES account_invites(id) ON DELETE RESTRICT,
    user_id INTEGER NOT NULL UNIQUE
        REFERENCES account_users(id) ON DELETE RESTRICT,
    accepted_at TEXT NOT NULL
);

CREATE INDEX account_invitation_acceptances_accepted_at_idx
    ON account_invitation_acceptances(accepted_at);

WITH request_candidates AS (
    SELECT request.public_number,
           invite.id AS invite_id,
           member.id AS user_id,
           invite.claimed_at,
           ROW_NUMBER() OVER (
               PARTITION BY request.id
               ORDER BY
                   ABS(
                       CAST(strftime('%s', invite.claimed_at) AS INTEGER)
                       - CAST(strftime('%s', member.created_at) AS INTEGER)
                   ),
                   invite.id DESC
           ) AS candidate_rank
      FROM account_access_requests AS request
      JOIN account_invites AS invite
        ON invite.access_request_id = request.id
       AND invite.kind = 'request'
      JOIN account_users AS member
        ON member.email = request.email
       AND member.role = 'member'
     WHERE request.status = 'claimed'
       AND request.public_number IS NOT NULL
       AND invite.claimed_at IS NOT NULL
)
INSERT INTO account_invitation_acceptances
    (public_number, invite_id, user_id, accepted_at)
SELECT public_number, invite_id, user_id, claimed_at
  FROM request_candidates
 WHERE candidate_rank = 1;

WITH direct_candidates AS (
    SELECT invite.id AS invite_id,
           member.id AS user_id,
           invite.claimed_at,
           ABS(
               CAST(strftime('%s', invite.claimed_at) AS INTEGER)
               - CAST(strftime('%s', member.created_at) AS INTEGER)
           ) AS claim_delta,
           ROW_NUMBER() OVER (
               PARTITION BY invite.id
               ORDER BY
                   ABS(
                       CAST(strftime('%s', invite.claimed_at) AS INTEGER)
                       - CAST(strftime('%s', member.created_at) AS INTEGER)
                   ),
                   member.id
           ) AS invite_rank,
           ROW_NUMBER() OVER (
               PARTITION BY member.id
               ORDER BY
                   ABS(
                       CAST(strftime('%s', invite.claimed_at) AS INTEGER)
                       - CAST(strftime('%s', member.created_at) AS INTEGER)
                   ),
                   invite.id
           ) AS member_rank
      FROM account_invites AS invite
      JOIN account_users AS member
        ON member.role = 'member'
      LEFT JOIN account_invitation_acceptances AS accepted
        ON accepted.user_id = member.id
     WHERE invite.kind = 'direct'
       AND invite.claimed_at IS NOT NULL
       AND accepted.user_id IS NULL
),
matched_direct_claims AS (
    SELECT invite_id,
           user_id,
           claimed_at,
           ROW_NUMBER() OVER (
               ORDER BY datetime(claimed_at), invite_id
           ) - 1 AS number_offset
      FROM direct_candidates
     WHERE claim_delta <= 2
       AND invite_rank = 1
       AND member_rank = 1
)
INSERT INTO account_invitation_acceptances
    (public_number, invite_id, user_id, accepted_at)
SELECT sequence.next_number + matched.number_offset,
       matched.invite_id,
       matched.user_id,
       matched.claimed_at
  FROM matched_direct_claims AS matched
 CROSS JOIN account_request_number_sequence AS sequence
 WHERE sequence.singleton = 1;

UPDATE account_request_number_sequence
   SET next_number = MAX(
       next_number,
       (
           SELECT COALESCE(MAX(public_number), 0) + 1
             FROM account_invitation_acceptances
       )
   )
 WHERE singleton = 1;
