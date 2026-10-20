-- Workspace links are reusable until their existing 72-hour expiry.
UPDATE workspace_access_tokens
SET consumed_at = NULL
WHERE expires_at > NOW();
