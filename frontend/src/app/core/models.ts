export type MissionType = 'main' | 'side' | 'daily' | 'timed' | 'home';
export type Category = 'fitness' | 'explore' | 'social' | 'habit';
export type TravelMode = 'driving' | 'walking' | 'two-wheeler' | 'transit';

export interface Tokens {
  access_token: string;
  refresh_token: string;
}

export interface Me {
  id: number;
  username: string;
  email: string;
  level: number;
  xp: number;
  xp_into_level: number;
  xp_for_next: number;
  cash: number;
  streak_days: number;
  streak_multiplier: number;
  slacker_stars: number;
  travel_mode: TravelMode;
  map_skin: 'default' | 'night' | 'retro' | 'neon';
  is_premium: boolean;
  xp_frozen: boolean;
  slots_total: number;
  slots_used: number;
  stats: { stamina: number; explorer: number; social: number; discipline: number };
  unlocks: Record<string, string>;
  next_unlocks: { level: number; feature_key: string; label: string }[];
  explored_cells: number;
  completed_missions: number;
}

export interface Mission {
  id: number;
  owner_id: number;
  title: string;
  description: string | null;
  type: MissionType;
  category: Category;
  lat: number | null;
  lng: number | null;
  place_name: string | null;
  radius_m: number;
  dwell_min: number;
  proof_required: boolean;
  deadline: string | null;
  status: string;
  story_id: number | null;
  chapter_no: number | null;
  visibility: 'private' | 'friends' | 'public';
  source: string;
  sponsored: boolean;
  squad_id: number | null;
  has_proof: boolean;
  dwell_started_at: string | null;
  distance_m: number | null;
  waypoint_url: string | null;
  view_url: string | null;
  created_at: string;
}

export interface MissionInput {
  title: string;
  description?: string | null;
  type: MissionType;
  category: Category;
  lat?: number | null;
  lng?: number | null;
  place_name?: string | null;
  radius_m: number;
  dwell_min: number;
  proof_required: boolean;
  deadline?: string | null;
  visibility?: 'private' | 'friends' | 'public';
}

export interface CheckinResult {
  status: 'completed' | 'in_progress' | 'rejected';
  reason: string | null;
  distance_m: number | null;
  dwell_remaining_min: number | null;
  xp_awarded: number;
  cash_awarded: number;
  level: number | null;
  level_up: boolean;
  new_unlocks: { level: number; feature_key: string; label: string }[];
  streak_days: number | null;
  district_claimed: string | null;
}

export interface Story {
  id: number;
  title: string;
  status: string;
  chapters: Mission[];
}

export interface Friend {
  user_id: number;
  username: string;
  level: number;
  status: 'pending' | 'accepted';
  direction: 'incoming' | 'outgoing' | 'mutual';
  weekly_xp: number;
}

export interface Squad {
  id: number;
  name: string;
  members: { user_id: number; username: string; level: number; role: string }[];
  my_role: string;
}

export interface Position {
  lat: number;
  lng: number;
  accuracy: number;
  speedKmh: number | null;
  timestamp: number;
}

export const TYPE_LABEL: Record<MissionType, string> = {
  main: 'Main story',
  side: 'Side mission',
  daily: 'Daily event',
  timed: 'Timed',
  home: 'Home',
};

export const TYPE_ICON: Record<MissionType, string> = {
  main: '★',
  side: '◆',
  daily: '☀',
  timed: '⏱',
  home: '⌂',
};
