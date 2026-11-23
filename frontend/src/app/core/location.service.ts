import { HttpClient } from '@angular/common/http';
import { Injectable, computed, inject, signal } from '@angular/core';
import { API } from './api';
import { Position } from './models';

const EXPLORE_EVERY_MS = 2 * 60 * 1000; // batched, foreground only — no background tracking
const DRIVING_LOCK_KMH = 20;

function haversineM(a: { lat: number; lng: number }, b: { lat: number; lng: number }): number {
  const R = 6371000;
  const toRad = (d: number) => (d * Math.PI) / 180;
  const dLat = toRad(b.lat - a.lat);
  const dLng = toRad(b.lng - a.lng);
  const h = Math.sin(dLat / 2) ** 2 + Math.cos(toRad(a.lat)) * Math.cos(toRad(b.lat)) * Math.sin(dLng / 2) ** 2;
  return 2 * R * Math.asin(Math.sqrt(h));
}

@Injectable({ providedIn: 'root' })
export class LocationService {
  private http = inject(HttpClient);

  readonly position = signal<Position | null>(null);
  readonly error = signal<string | null>(null);
  /** Map and check-in UI lock above 20 km/h ("don't play while driving"). */
  readonly drivingLock = computed(() => (this.position()?.speedKmh ?? 0) > DRIVING_LOCK_KMH);

  private watchId: number | null = null;
  private buffer: { lat: number; lng: number; accuracy_m: number; timestamp: string }[] = [];
  private timer: ReturnType<typeof setInterval> | null = null;
  private visibilityHandler = () => (document.hidden ? this.stop() : this.start());

  start() {
    if (this.watchId !== null || !('geolocation' in navigator)) {
      if (!('geolocation' in navigator)) this.error.set('This device has no GPS / geolocation');
      return;
    }
    document.addEventListener('visibilitychange', this.visibilityHandler);
    this.watchId = navigator.geolocation.watchPosition(
      (p) => this.onPosition(p),
      (e) => this.error.set(e.message || 'Location permission denied'),
      { enableHighAccuracy: true, maximumAge: 5000, timeout: 20000 },
    );
    this.timer = setInterval(() => this.flush(), EXPLORE_EVERY_MS);
  }

  stop() {
    if (this.watchId !== null) navigator.geolocation.clearWatch(this.watchId);
    this.watchId = null;
    if (this.timer) clearInterval(this.timer);
    this.timer = null;
    this.flush();
  }

  distanceTo(lat: number, lng: number): number | null {
    const p = this.position();
    return p ? haversineM(p, { lat, lng }) : null;
  }

  private onPosition(p: GeolocationPosition) {
    const prev = this.position();
    const next: Position = {
      lat: p.coords.latitude,
      lng: p.coords.longitude,
      accuracy: p.coords.accuracy,
      speedKmh: p.coords.speed != null ? p.coords.speed * 3.6 : null,
      timestamp: p.timestamp,
    };
    if (next.speedKmh === null && prev) {
      const dt = (next.timestamp - prev.timestamp) / 1000;
      if (dt > 2) next.speedKmh = (haversineM(prev, next) / dt) * 3.6;
    }
    this.error.set(null);
    this.position.set(next);
    const last = this.buffer[this.buffer.length - 1];
    const first = !prev;
    if (!last || haversineM(last, next) > 50) {
      this.buffer.push({
        lat: next.lat,
        lng: next.lng,
        accuracy_m: next.accuracy,
        timestamp: new Date(next.timestamp).toISOString(),
      });
    }
    if (first) this.flush(); // clear the fog where the player opened the app
  }

  private flush() {
    if (!this.buffer.length) return;
    const points = this.buffer.splice(0, 200);
    this.http.post(`${API}/explore`, { points }).subscribe({ error: () => this.buffer.unshift(...points) });
  }
}
