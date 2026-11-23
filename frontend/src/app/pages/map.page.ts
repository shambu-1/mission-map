import type { Feature, FeatureCollection, Polygon } from 'geojson';
import { Component, ElementRef, OnDestroy, afterNextRender, computed, effect, inject, signal, viewChild } from '@angular/core';
import { Router } from '@angular/router';
import { AttributionControl, GeoJSONSource, Map as MlMap, Marker } from 'maplibre-gl';
import { GameService } from '../core/game.service';
import { LocationService } from '../core/location.service';
import { MAP_STYLES, RASTER_FALLBACK, circlePolygon, fogPolygon, formatDistance } from '../core/map-utils';
import { Mission, TYPE_ICON } from '../core/models';
import { HudComponent } from '../shared/hud.component';
import { MissionSheetComponent } from '../shared/mission-sheet.component';

const DEFAULT_CENTER: [number, number] = [77.5946, 12.9716]; // Bengaluru until GPS arrives

@Component({
  selector: 'app-map',
  imports: [HudComponent, MissionSheetComponent],
  template: `
    <div class="relative h-full w-full" [class]="'skin-' + (game.me()?.map_skin ?? 'default')">
      <div class="absolute inset-0"><div #mapEl class="h-full w-full"></div></div>

      <!-- HUD -->
      <div class="pointer-events-none absolute inset-x-0 top-0 flex flex-col gap-2 p-3">
        <div class="pointer-events-auto"><app-hud /></div>

        <!-- Mini-map HUD: nearest 3 missions -->
        @if (nearest().length) {
          <div class="pointer-events-auto w-56 rounded-box bg-base-100/85 p-2 text-xs shadow-lg backdrop-blur">
            @for (n of nearest(); track n.m.id) {
              <button class="flex w-full items-center gap-2 rounded px-1 py-1 text-left hover:bg-base-300" (click)="select(n.m)">
                <span [class]="colorOf(n.m)">{{ icon[n.m.type] }}</span>
                <span class="flex-1 truncate">{{ n.m.title }}</span>
                <span class="font-mono text-base-content/70">{{ fmt(n.d) }}</span>
              </button>
            }
          </div>
        }
      </div>

      <!-- Layer toggles + actions -->
      <div class="absolute right-3 top-28 flex flex-col gap-2">
        <button class="btn btn-sm btn-circle" [class.btn-primary]="showFog()" (click)="toggleFog()" title="Fog of war">☁</button>
        <button class="btn btn-sm btn-circle" [class.btn-primary]="showDistricts()" (click)="toggleDistricts()" title="Districts">▦</button>
        <button class="btn btn-sm btn-circle" [class.btn-primary]="showHeat()" (click)="toggleHeat()" title="Personal heatmap">♨</button>
        @if (game.me()?.unlocks?.['daily_events'] === 'true') {
          <button class="btn btn-sm btn-circle btn-accent" (click)="loadDaily()" title="Daily random events">☀</button>
        }
        <button class="btn btn-sm btn-circle" (click)="recenter()" title="My location">◎</button>
      </div>

      <button class="btn btn-primary btn-circle btn-lg absolute bottom-4 right-4 text-2xl shadow-xl" (click)="router.navigateByUrl('/missions/new')" aria-label="New mission">
        +
      </button>

      @if (location.error()) {
        <div class="alert alert-warning absolute bottom-4 left-4 right-24 py-2 text-xs">{{ location.error() }}</div>
      }
      @if (toast()) {
        <div class="toast toast-top toast-center z-50"><div class="alert alert-info text-sm">{{ toast() }}</div></div>
      }

      <!-- Tap on map → create mission here -->
      @if (picked(); as p) {
        <div class="absolute inset-x-3 bottom-20 z-20 flex items-center gap-2 rounded-box bg-base-200 p-3 shadow-xl">
          <span class="flex-1 text-sm">Pin a mission here?</span>
          <button class="btn btn-sm btn-primary" (click)="createAt(p)">Create</button>
          <button class="btn btn-sm btn-ghost" (click)="picked.set(null)">✕</button>
        </div>
      }

      @if (location.drivingLock()) {
        <div class="absolute inset-0 z-30 flex flex-col items-center justify-center gap-3 bg-black/85 p-8 text-center">
          <div class="hud-font text-4xl font-bold text-error">DON'T PLAY WHILE DRIVING</div>
          <p class="text-sm text-white/70">The map unlocks when you're moving under 20 km/h.</p>
        </div>
      }

      @if (selected(); as m) {
        <div class="absolute inset-x-0 bottom-0 z-40 max-h-[75%] overflow-y-auto">
          <app-mission-sheet [mission]="m" (closed)="select(null)" (changed)="reload()" />
        </div>
      }
    </div>
  `,
})
export class MapPage implements OnDestroy {
  protected game = inject(GameService);
  protected location = inject(LocationService);
  protected router = inject(Router);
  protected icon = TYPE_ICON;

  private mapEl = viewChild.required<ElementRef<HTMLDivElement>>('mapEl');
  private map?: MlMap;
  private markers = new Map<number, Marker>();
  private meMarker?: Marker;
  private centeredOnce = false;
  private currentSkin = 'default';
  private lastLoadCenter: { lat: number; lng: number } | null = null;

  protected selected = signal<Mission | null>(null);
  protected picked = signal<{ lat: number; lng: number } | null>(null);
  protected toast = signal<string | null>(null);
  protected showFog = signal(localStorage.getItem('mm_fog') !== '0');
  protected showDistricts = signal(false);
  protected showHeat = signal(false);
  private styleReady = signal(false);

  protected nearest = computed(() => {
    const pos = this.location.position();
    const list = this.game.missions().filter((m) => m.lat !== null && ['active', 'in_progress'].includes(m.status));
    return list
      .map((m) => ({ m, d: pos ? this.location.distanceTo(m.lat!, m.lng!)! : (m.distance_m ?? Infinity) }))
      .sort((a, b) => a.d - b.d)
      .slice(0, 3);
  });

  constructor() {
    afterNextRender(() => this.initMap());

    // Re-draw markers when missions or position change
    effect(() => {
      const missions = this.game.missions();
      this.location.position();
      if (this.map) this.renderMarkers(missions);
    });

    // Follow GPS; reload missions after moving > 1 km
    effect(() => {
      const pos = this.location.position();
      if (!pos || !this.map) return;
      if (!this.meMarker) this.meMarker = new Marker({ element: this.meElement() }).addTo(this.map.setCenter(this.map.getCenter()));
      this.meMarker.setLngLat([pos.lng, pos.lat]);
      if (!this.centeredOnce) {
        this.centeredOnce = true;
        this.map.jumpTo({ center: [pos.lng, pos.lat], zoom: 15 });
      }
      const last = this.lastLoadCenter;
      if (!last || (this.location.distanceTo(last.lat, last.lng) ?? 0) > 1000) {
        this.lastLoadCenter = { lat: pos.lat, lng: pos.lng };
        this.reload();
      }
    });

    // Map skin → style
    effect(() => {
      const skin = this.game.me()?.map_skin ?? 'default';
      if (this.map && skin !== this.currentSkin) {
        this.currentSkin = skin;
        this.styleReady.set(false);
        this.map.setStyle(MAP_STYLES[skin] as string);
      }
    });

    // Geofence ring of the selected mission
    effect(() => {
      const m = this.selected();
      if (!this.styleReady()) return;
      const src = this.map?.getSource('geofence') as GeoJSONSource | undefined;
      src?.setData(
        m && m.lat !== null
          ? circlePolygon(m.lat, m.lng!, m.radius_m)
          : { type: 'FeatureCollection', features: [] },
      );
    });
  }

  ngOnDestroy() {
    this.map?.remove();
  }

  private initMap() {
    const skin = this.game.me()?.map_skin ?? 'default';
    this.currentSkin = skin;
    this.map = new MlMap({
      container: this.mapEl().nativeElement,
      style: MAP_STYLES[skin] as string,
      center: DEFAULT_CENTER,
      zoom: 13,
      attributionControl: false,
    });
    this.map.addControl(new AttributionControl({ compact: true }), 'bottom-left');
    this.map.on('error', (e) => {
      // Vector style unreachable → OSM raster fallback
      if (!this.styleReady() && String(e.error?.message ?? '').includes('style')) this.map!.setStyle(RASTER_FALLBACK);
    });
    this.map.on('style.load', () => {
      this.addLayers();
      this.styleReady.set(true);
      this.refreshOverlays();
    });
    this.map.on('moveend', () => this.refreshOverlays());
    this.map.on('click', (e) => {
      if (this.selected()) return this.select(null);
      this.picked.set({ lat: e.lngLat.lat, lng: e.lngLat.lng });
    });
    this.reload();
  }

  private addLayers() {
    const map = this.map!;
    const empty: FeatureCollection = { type: 'FeatureCollection', features: [] };
    map.addSource('districts', { type: 'geojson', data: empty });
    map.addLayer({
      id: 'districts-fill',
      type: 'fill',
      source: 'districts',
      layout: { visibility: this.showDistricts() ? 'visible' : 'none' },
      paint: { 'fill-color': ['case', ['get', 'claimed'], '#f5b700', '#3b82f6'], 'fill-opacity': ['case', ['get', 'claimed'], 0.3, 0.08] },
    });
    map.addLayer({
      id: 'districts-line',
      type: 'line',
      source: 'districts',
      layout: { visibility: this.showDistricts() ? 'visible' : 'none' },
      paint: { 'line-color': '#f5b700', 'line-width': 1.5, 'line-dasharray': [2, 2] },
    });

    map.addSource('heat', { type: 'geojson', data: empty });
    map.addLayer({
      id: 'heat',
      type: 'heatmap',
      source: 'heat',
      layout: { visibility: this.showHeat() ? 'visible' : 'none' },
      paint: { 'heatmap-radius': 30, 'heatmap-opacity': 0.75 },
    });

    map.addSource('fog', { type: 'geojson', data: empty });
    map.addLayer({
      id: 'fog',
      type: 'fill',
      source: 'fog',
      layout: { visibility: this.showFog() ? 'visible' : 'none' },
      paint: { 'fill-color': '#05070a', 'fill-opacity': 0.55 },
    });

    map.addSource('geofence', { type: 'geojson', data: empty });
    map.addLayer({ id: 'geofence-fill', type: 'fill', source: 'geofence', paint: { 'fill-color': '#f5b700', 'fill-opacity': 0.12 } });
    map.addLayer({ id: 'geofence-line', type: 'line', source: 'geofence', paint: { 'line-color': '#f5b700', 'line-width': 2 } });
  }

  private bboxString(): string | null {
    if (!this.map) return null;
    const b = this.map.getBounds();
    return [b.getWest(), b.getSouth(), b.getEast(), b.getNorth()].map((n) => n.toFixed(5)).join(',');
  }

  /** Visible area, clamped around the centre to the API's max size (0.5° per side). */
  private exploredBbox(): string | null {
    if (!this.map) return null;
    const b = this.map.getBounds();
    const c = this.map.getCenter();
    const half = 0.24;
    const w = Math.max(b.getWest(), c.lng - half);
    const e = Math.min(b.getEast(), c.lng + half);
    const s = Math.max(b.getSouth(), c.lat - half);
    const n = Math.min(b.getNorth(), c.lat + half);
    return [w, s, e, n].map((v) => v.toFixed(5)).join(',');
  }

  private refreshOverlays() {
    const bbox = this.bboxString();
    if (!bbox || !this.styleReady()) return;
    const b = this.map!.getBounds();
    if (this.showFog() && this.map!.getZoom() >= 11) {
      this.game.explored(this.exploredBbox()!).subscribe((fc) => {
        (this.map?.getSource('fog') as GeoJSONSource | undefined)?.setData(
          fogPolygon([b.getWest(), b.getSouth(), b.getEast(), b.getNorth()], fc),
        );
      });
    }
    if (this.showDistricts()) {
      this.game.districts(bbox).subscribe((fc) => (this.map?.getSource('districts') as GeoJSONSource | undefined)?.setData(fc));
    }
  }

  protected reload() {
    const pos = this.location.position();
    this.game.loadMissions(pos?.lat, pos?.lng, 20000).subscribe((list) => {
      const sel = this.selected();
      if (sel) this.selected.set(list.find((m) => m.id === sel.id) ?? null);
    });
  }

  protected loadDaily() {
    const pos = this.location.position();
    if (!pos) return this.flash('Waiting for GPS…');
    this.game.daily(pos.lat, pos.lng).subscribe({
      next: (list) => {
        this.flash(list.length ? `${list.length} daily events nearby — expire at midnight` : 'No places found nearby today');
        this.reload();
      },
      error: () => this.flash('Could not load daily events'),
    });
  }

  private renderMarkers(missions: Mission[]) {
    const keep = new Set<number>();
    for (const m of missions) {
      if (m.lat === null || m.lng === null || !['active', 'in_progress'].includes(m.status)) continue;
      keep.add(m.id);
      const dist = this.location.distanceTo(m.lat, m.lng);
      const inside = dist !== null && dist <= m.radius_m;
      let marker = this.markers.get(m.id);
      if (!marker) {
        const el = document.createElement('div');
        el.addEventListener('click', (ev) => {
          ev.stopPropagation();
          this.select(this.game.missions().find((x) => x.id === m.id) ?? m);
        });
        marker = new Marker({ element: el }).setLngLat([m.lng, m.lat]).addTo(this.map!);
        this.markers.set(m.id, marker);
      }
      const el = marker.getElement();
      el.className = `mm-marker ${m.type}${m.sponsored ? ' sponsored' : ''}${inside ? ' inside' : ''}`;
      el.textContent = TYPE_ICON[m.type];
      el.title = m.title;
      marker.setLngLat([m.lng, m.lat]);
    }
    for (const [id, marker] of this.markers) {
      if (!keep.has(id)) {
        marker.remove();
        this.markers.delete(id);
      }
    }
  }

  protected select(m: Mission | null) {
    this.picked.set(null);
    this.selected.set(m);
    if (m?.lat != null && this.map) this.map.easeTo({ center: [m.lng!, m.lat], zoom: Math.max(this.map.getZoom(), 15) });
  }

  protected createAt(p: { lat: number; lng: number }) {
    this.router.navigate(['/missions/new'], { queryParams: { lat: p.lat.toFixed(6), lng: p.lng.toFixed(6) } });
  }

  protected recenter() {
    const pos = this.location.position();
    if (pos) this.map?.easeTo({ center: [pos.lng, pos.lat], zoom: 16 });
  }

  protected toggleFog() {
    this.showFog.update((v) => !v);
    localStorage.setItem('mm_fog', this.showFog() ? '1' : '0');
    this.map?.setLayoutProperty('fog', 'visibility', this.showFog() ? 'visible' : 'none');
    this.refreshOverlays();
  }

  protected toggleDistricts() {
    this.showDistricts.update((v) => !v);
    for (const id of ['districts-fill', 'districts-line']) {
      this.map?.setLayoutProperty(id, 'visibility', this.showDistricts() ? 'visible' : 'none');
    }
    this.refreshOverlays();
  }

  protected toggleHeat() {
    this.showHeat.update((v) => !v);
    this.map?.setLayoutProperty('heat', 'visibility', this.showHeat() ? 'visible' : 'none');
    if (this.showHeat()) this.game.heatmap().subscribe((fc) => (this.map?.getSource('heat') as GeoJSONSource | undefined)?.setData(fc));
  }

  protected colorOf(m: Mission) {
    return { main: 'text-primary', side: 'text-secondary', daily: 'text-accent', timed: 'text-error', home: 'text-base-content' }[m.type];
  }

  protected fmt = formatDistance;

  private flash(text: string) {
    this.toast.set(text);
    setTimeout(() => this.toast.set(null), 3500);
  }

  private meElement() {
    const el = document.createElement('div');
    el.className = 'mm-me';
    return el;
  }
}
