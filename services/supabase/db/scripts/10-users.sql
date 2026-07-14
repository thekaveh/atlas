-- 10-users.sql
-- OWNER: backend/auth — public.users. FK-referenced by the research (13) and
-- memory (14) slices, so this MUST sort before them. Only this service's
-- objects belong here. Moved verbatim from the former 05-public-tables.sql.

CREATE TABLE IF NOT EXISTS public.users (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL,
    created_at TIMESTAMPTZ DEFAULT now()
);

-- Keep the shared ownership table aligned with Supabase Auth. Memory and
-- research use public.users as their foreign-key target, while JWT subjects
-- originate in auth.users. The trigger covers future inserts and profile
-- updates; the backfill makes the contract true for existing installations.
CREATE OR REPLACE FUNCTION public.handle_auth_user_sync()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = ''
AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        DELETE FROM public.users WHERE id = OLD.id;
        RETURN OLD;
    END IF;
    INSERT INTO public.users (id, name, created_at)
    VALUES (
        NEW.id,
        COALESCE(
            NULLIF(BTRIM(NEW.raw_user_meta_data ->> 'name'), ''),
            NULLIF(BTRIM(NEW.raw_user_meta_data ->> 'full_name'), ''),
            NULLIF(SPLIT_PART(COALESCE(NEW.email, ''), '@', 1), ''),
            'Atlas user'
        ),
        COALESCE(NEW.created_at, now())
    )
    ON CONFLICT (id) DO UPDATE
    SET name = EXCLUDED.name;
    RETURN NEW;
END;
$$;

-- Trigger functions are not client RPCs. The public schema's broad default
-- function grants would otherwise expose this SECURITY DEFINER function.
REVOKE ALL ON FUNCTION public.handle_auth_user_sync()
    FROM PUBLIC, anon, authenticated, service_role;

DROP TRIGGER IF EXISTS on_auth_user_sync ON auth.users;
CREATE TRIGGER on_auth_user_sync
    AFTER INSERT OR DELETE OR UPDATE OF email, raw_user_meta_data ON auth.users
    FOR EACH ROW EXECUTE FUNCTION public.handle_auth_user_sync();

INSERT INTO public.users (id, name, created_at)
SELECT
    id,
    COALESCE(
        NULLIF(BTRIM(raw_user_meta_data ->> 'name'), ''),
        NULLIF(BTRIM(raw_user_meta_data ->> 'full_name'), ''),
        NULLIF(SPLIT_PART(COALESCE(email, ''), '@', 1), ''),
        'Atlas user'
    ),
    COALESCE(created_at, now())
FROM auth.users
ON CONFLICT (id) DO UPDATE
SET name = EXCLUDED.name;

ALTER TABLE public.users ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "Users can read own profile" ON public.users;
CREATE POLICY "Users can read own profile" ON public.users
    FOR SELECT USING (auth.uid() = id);

DROP POLICY IF EXISTS "Users can update own profile" ON public.users;
CREATE POLICY "Users can update own profile" ON public.users
    FOR UPDATE USING (auth.uid() = id) WITH CHECK (auth.uid() = id);

DROP POLICY IF EXISTS "Service role can access all user profiles" ON public.users;
CREATE POLICY "Service role can access all user profiles" ON public.users
    FOR ALL USING (auth.role() = 'service_role')
    WITH CHECK (auth.role() = 'service_role');
