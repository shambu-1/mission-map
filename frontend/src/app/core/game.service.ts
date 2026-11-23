import type { Feature, FeatureCollection, Polygon } from 'geojson';
import { HttpClient, HttpParams } from '@angular/common/http';
import { Injectable, inject, signal } from '@angular/core';
import { Observable, tap } from 'rxjs';
import { API } from './api';
import { CheckinResult, Friend, Me, Mission, MissionInput, Squad, Story } from './models';

/** Thin API client + shared game state (signals). */
@Injectable({ providedIn: 'root' })
export class GameService {
  private http = inject(HttpClient);

  readonly me = signal<Me | null>(null);
  readonly missions = signal<Mission[]>([]);
  /** Last check-in that completed a mission — drives the "Mission passed" overlay. */
  readonly passed = signal<(CheckinResult & { title: string }) | null>(null);

  // ---------- Me ----------
  loadMe() {
    return this.http.get<Me>(`${API}/me`).pipe(tap((m) => this.me.set(m)));
  }
  updateMe(body: Partial<Pick<Me, 'travel_mode' | 'map_skin'>>) {
    return this.http.patch<Me>(`${API}/me`, body).pipe(tap((m) => this.me.set(m)));
  }
  shop() {
    return this.http.get<{ item: string; price: number; level: number; owned: boolean }[]>(`${API}/me/shop`);
  }
  buy(item: string) {
    return this.http.post<Me>(`${API}/me/shop`, { item }).pipe(tap((m) => this.me.set(m)));
  }
  history() {
    return this.http.get<{ mission_id: number; title: string; type: string; xp_awarded: number; completed_at: string; proof_url: string | null }[]>(
      `${API}/me/history`,
    );
  }
  heatmap() {
    return this.http.get<FeatureCollection>(`${API}/me/heatmap`);
  }
  deleteAccount() {
    return this.http.delete(`${API}/me`);
  }
  unlockTable() {
    return this.http.get<{ level: number; feature_key: string; label: string }[]>(`${API}/unlocks`);
  }

  // ---------- Missions ----------
  loadMissions(lat?: number, lng?: number, radius = 10000) {
    let params = new HttpParams();
    if (lat != null && lng != null) params = params.set('lat', lat).set('lng', lng).set('radius', radius);
    return this.http.get<Mission[]>(`${API}/missions`, { params }).pipe(tap((m) => this.missions.set(m)));
  }
  mission(id: number) {
    return this.http.get<Mission>(`${API}/missions/${id}`);
  }
  create(body: MissionInput) {
    return this.http.post<Mission>(`${API}/missions`, body);
  }
  update(id: number, body: Partial<MissionInput>) {
    return this.http.patch<Mission>(`${API}/missions/${id}`, body);
  }
  abandon(id: number) {
    return this.http.delete(`${API}/missions/${id}`);
  }
  importLink(url: string) {
    return this.http.post<{ lat: number | null; lng: number | null; place_name: string | null; needs_manual_pin: boolean }>(
      `${API}/missions/import-link`,
      { url },
    );
  }
  checkin(id: number, body: { lat?: number; lng?: number; accuracy_m?: number; timestamp?: string; manual?: boolean }) {
    return this.http.post<CheckinResult>(`${API}/missions/${id}/checkin`, body);
  }
  uploadProof(id: number, file: File | null, note: string | null) {
    const form = new FormData();
    if (file) form.append('file', file);
    if (note) form.append('note', note);
    return this.http.post<Mission>(`${API}/missions/${id}/proof`, form);
  }
  take(id: number) {
    return this.http.post<Mission>(`${API}/missions/${id}/take`, {});
  }
  report(id: number, reason: string) {
    return this.http.post(`${API}/missions/${id}/report`, { reason });
  }
  inbox() {
    return this.http.get<Mission[]>(`${API}/missions/inbox`);
  }
  answerInbox(id: number, accept: boolean) {
    return this.http.post<Mission | null>(`${API}/missions/inbox/${id}`, { accept });
  }
  toVerify() {
    return this.http.get<Mission[]>(`${API}/missions/to-verify/list`);
  }
  verify(id: number) {
    return this.http.post<Mission>(`${API}/missions/${id}/verify`, {});
  }

  // ---------- Stories / daily ----------
  stories() {
    return this.http.get<Story[]>(`${API}/stories`);
  }
  createStory(title: string, chapters: Partial<MissionInput>[]) {
    return this.http.post<Story>(`${API}/stories`, { title, chapters });
  }
  daily(lat: number, lng: number) {
    return this.http.get<Mission[]>(`${API}/daily`, { params: { lat, lng } });
  }

  // ---------- Map layers ----------
  explored(bbox: string) {
    return this.http.get<FeatureCollection<Polygon>>(`${API}/map/explored`, { params: { bbox } });
  }
  districts(bbox: string) {
    return this.http.get<FeatureCollection>(`${API}/districts`, { params: { bbox } });
  }

  // ---------- Social ----------
  friends() {
    return this.http.get<Friend[]>(`${API}/friends`);
  }
  addFriend(username: string) {
    return this.http.post<Friend>(`${API}/friends`, { username });
  }
  acceptFriend(id: number) {
    return this.http.post<Friend>(`${API}/friends/${id}/accept`, {});
  }
  removeFriend(id: number) {
    return this.http.delete(`${API}/friends/${id}`);
  }
  sendMission(friendId: number, body: MissionInput) {
    return this.http.post<Mission>(`${API}/friends/${friendId}/missions`, body);
  }
  leaderboard() {
    return this.http.get<{ user_id: number; username: string; level: number; weekly_xp: number }[]>(`${API}/leaderboard`);
  }
  squads() {
    return this.http.get<Squad[]>(`${API}/squads`);
  }
  createSquad(name: string, member_usernames: string[]) {
    return this.http.post<Squad>(`${API}/squads`, { name, member_usernames });
  }
  leaveSquad(id: number) {
    return this.http.delete(`${API}/squads/${id}/leave`);
  }
  squadMissions(id: number) {
    return this.http.get<Mission[]>(`${API}/squads/${id}/missions`);
  }
  createSquadMission(id: number, body: MissionInput): Observable<Mission> {
    return this.http.post<Mission>(`${API}/squads/${id}/missions`, body);
  }
}

/** Human readable API error. */
export function errMsg(e: unknown): string {
  const err = e as { error?: { detail?: unknown }; message?: string };
  const d = err?.error?.detail;
  if (typeof d === 'string') return d;
  if (Array.isArray(d) && d[0]?.msg) return d[0].msg;
  return err?.message ?? 'Something went wrong';
}
