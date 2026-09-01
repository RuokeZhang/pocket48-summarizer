ALTER TABLE room_voice_processing_jobs
ADD COLUMN channel_id TEXT;

ALTER TABLE room_voice_processing_jobs
ADD COLUMN server_id TEXT;
