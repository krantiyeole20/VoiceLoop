-- =============================================================================
-- VoiceLoop — Supabase schema
-- Run once on a fresh project. All statements are idempotent (IF NOT EXISTS).
-- =============================================================================

-- Ensure pgcrypto is available for gen_random_uuid()
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- =============================================================================
-- USERS — mirrors auth.users. Trigger keeps in sync.
-- =============================================================================
CREATE TABLE IF NOT EXISTS public.users (
    id          uuid PRIMARY KEY REFERENCES auth.users(id) ON DELETE CASCADE,
    email       text UNIQUE NOT NULL,
    name        text,
    google_id   text,
    picture_url text,
    created_at  timestamptz DEFAULT now()
);

-- Trigger: create a public.users row whenever an auth.users row is inserted.
CREATE OR REPLACE FUNCTION public.handle_new_auth_user()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
BEGIN
    INSERT INTO public.users (id, email, name, google_id, picture_url)
    VALUES (
        NEW.id,
        NEW.email,
        COALESCE(NEW.raw_user_meta_data ->> 'full_name', NEW.raw_user_meta_data ->> 'name'),
        NEW.raw_user_meta_data ->> 'sub',
        NEW.raw_user_meta_data ->> 'avatar_url'
    )
    ON CONFLICT (id) DO UPDATE
       SET email      = EXCLUDED.email,
           name       = COALESCE(EXCLUDED.name, public.users.name),
           picture_url = COALESCE(EXCLUDED.picture_url, public.users.picture_url);
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS on_auth_user_created ON auth.users;
CREATE TRIGGER on_auth_user_created
    AFTER INSERT OR UPDATE ON auth.users
    FOR EACH ROW EXECUTE FUNCTION public.handle_new_auth_user();

-- =============================================================================
-- BYOK ENCRYPTED API KEYS
-- =============================================================================
CREATE TABLE IF NOT EXISTS public.user_api_keys (
    id                       uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id                  uuid UNIQUE NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
    livekit_url_encrypted    text NOT NULL,
    livekit_key_encrypted    text NOT NULL,
    livekit_secret_encrypted text NOT NULL,
    openai_key_encrypted     text NOT NULL,
    deepgram_key_encrypted   text NOT NULL,
    encryption_salt          text DEFAULT 'salt_v1',
    updated_at               timestamptz DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_user_api_keys_user ON public.user_api_keys(user_id);

-- =============================================================================
-- INTERVIEWS — one row per completed session
-- =============================================================================
CREATE TABLE IF NOT EXISTS public.interviews (
    id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id          uuid NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
    candidate_name   text,
    candidate_email  text,
    room_name        text,
    job_role         text,
    experience_level text,
    company_name     text,
    track            text NOT NULL,                -- intro|behavioral|technical_swe|ds_ml|analytics|product
    track_config     jsonb DEFAULT '{}'::jsonb,
    interview_date   timestamptz DEFAULT now(),
    final_stage      text,
    ended_by         text,                          -- natural_completion|user_disconnect|timeout
    skipped_stages   jsonb DEFAULT '[]'::jsonb,
    has_resume       bool DEFAULT false,
    has_jd           bool DEFAULT false,
    conversation     jsonb DEFAULT '{}'::jsonb,
    total_messages   jsonb DEFAULT '{}'::jsonb,
    stage_notes      jsonb DEFAULT '{}'::jsonb,    -- dict[stage_id → AgentNote]; NEW vs MockFlow
    metadata         jsonb DEFAULT '{}'::jsonb,
    created_at       timestamptz DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_interviews_user      ON public.interviews(user_id);
CREATE INDEX IF NOT EXISTS idx_interviews_room_name ON public.interviews(room_name);
CREATE INDEX IF NOT EXISTS idx_interviews_date      ON public.interviews(interview_date DESC);
CREATE INDEX IF NOT EXISTS idx_interviews_track     ON public.interviews(track);

-- =============================================================================
-- CANDIDATE FEEDBACK (existing, unchanged)
-- =============================================================================
CREATE TABLE IF NOT EXISTS public.feedback (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id       uuid NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
    interview_id  uuid NOT NULL REFERENCES public.interviews(id) ON DELETE CASCADE,
    feedback_data jsonb NOT NULL,
    created_at    timestamptz DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_feedback_interview ON public.feedback(interview_id);

-- =============================================================================
-- EVAL REPORTS (Phase 3 — Tier 4 of architecture)
-- =============================================================================
CREATE TABLE IF NOT EXISTS public.eval_reports (
    id                       uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    interview_id             uuid UNIQUE NOT NULL REFERENCES public.interviews(id) ON DELETE CASCADE,
    user_id                  uuid NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
    session_eval             jsonb NOT NULL,            -- full SessionEvalReport serialized
    overall_agent_pass       bool,
    overall_candidate_signal text,                       -- weak|moderate|strong
    rubric_version           int DEFAULT 1,
    created_at               timestamptz DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_eval_reports_interview ON public.eval_reports(interview_id);
CREATE INDEX IF NOT EXISTS idx_eval_reports_user      ON public.eval_reports(user_id);
CREATE INDEX IF NOT EXISTS idx_eval_reports_signal    ON public.eval_reports(overall_candidate_signal);

CREATE TABLE IF NOT EXISTS public.stage_eval_reports (
    id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    eval_report_id       uuid NOT NULL REFERENCES public.eval_reports(id) ON DELETE CASCADE,
    interview_id         uuid NOT NULL REFERENCES public.interviews(id) ON DELETE CASCADE,
    stage_id             text NOT NULL,
    stage_type           text NOT NULL,
    stage_index          int  NOT NULL,
    agent_face           jsonb NOT NULL,
    candidate_face       jsonb NOT NULL,
    programmatic_signals jsonb NOT NULL,
    agent_note           jsonb,
    note_schema_valid    bool DEFAULT true,
    created_at           timestamptz DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_stage_eval_eval_report ON public.stage_eval_reports(eval_report_id);
CREATE INDEX IF NOT EXISTS idx_stage_eval_interview   ON public.stage_eval_reports(interview_id);
CREATE INDEX IF NOT EXISTS idx_stage_eval_type        ON public.stage_eval_reports(stage_type);

-- =============================================================================
-- ROW LEVEL SECURITY
-- All app-side reads go through the service role (which bypasses RLS).
-- These policies protect against accidental anon-key access.
-- =============================================================================
ALTER TABLE public.users              ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.user_api_keys      ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.interviews         ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.feedback           ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.eval_reports       ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.stage_eval_reports ENABLE ROW LEVEL SECURITY;

-- Drop existing policies (idempotent re-run)
DROP POLICY IF EXISTS "users self select"               ON public.users;
DROP POLICY IF EXISTS "users self insert"               ON public.users;
DROP POLICY IF EXISTS "api_keys self all"               ON public.user_api_keys;
DROP POLICY IF EXISTS "interviews self all"             ON public.interviews;
DROP POLICY IF EXISTS "feedback self all"               ON public.feedback;
DROP POLICY IF EXISTS "eval_reports self all"           ON public.eval_reports;
DROP POLICY IF EXISTS "stage_eval_reports via parent"   ON public.stage_eval_reports;

CREATE POLICY "users self select" ON public.users
    FOR SELECT USING (auth.uid() = id);

CREATE POLICY "users self insert" ON public.users
    FOR INSERT WITH CHECK (auth.uid() = id);

CREATE POLICY "api_keys self all" ON public.user_api_keys
    FOR ALL USING (auth.uid() = user_id) WITH CHECK (auth.uid() = user_id);

CREATE POLICY "interviews self all" ON public.interviews
    FOR ALL USING (auth.uid() = user_id) WITH CHECK (auth.uid() = user_id);

CREATE POLICY "feedback self all" ON public.feedback
    FOR ALL USING (auth.uid() = user_id) WITH CHECK (auth.uid() = user_id);

CREATE POLICY "eval_reports self all" ON public.eval_reports
    FOR ALL USING (auth.uid() = user_id) WITH CHECK (auth.uid() = user_id);

CREATE POLICY "stage_eval_reports via parent" ON public.stage_eval_reports
    FOR ALL USING (
        eval_report_id IN (SELECT id FROM public.eval_reports WHERE user_id = auth.uid())
    );
