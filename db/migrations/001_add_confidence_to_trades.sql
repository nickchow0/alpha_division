-- Migration 001: add confidence column to trades table
-- Run once on the existing DB: psql -U $POSTGRES_USER alphadivision -f this_file.sql

ALTER TABLE trades
    ADD COLUMN IF NOT EXISTS confidence DECIMAL(4,3);
