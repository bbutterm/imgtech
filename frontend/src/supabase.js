import { createClient } from '@supabase/supabase-js'

// публичные значения (не секрет: они в любом случае попадают в браузер)
export const SUPABASE_URL = 'https://ajltzwomvyvntvtjnrrf.supabase.co'
const PUBLISHABLE_KEY = 'sb_publishable_avfKy6YbDfLLYmjiUF7zcw_a3tLPdR6'

export const supabase = createClient(SUPABASE_URL, PUBLISHABLE_KEY)
