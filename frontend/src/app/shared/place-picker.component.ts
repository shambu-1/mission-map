import { HttpClient } from '@angular/common/http';
import { Component, ElementRef, OnDestroy, afterNextRender, effect, inject, input, model, signal, viewChild } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { AttributionControl, GeoJSONSource, Map as MlMap, Marker } from 'maplibre-gl';
import { errMsg, GameService } from '../core/game.service';
import { LocationService } from '../core/location.service';
import { MAP_STYLES, circlePolygon } from '../core/map-utils';

export interface Place {
  lat: number;
  lng: number;
  name: string | null;
}

/** Three ways to set a mission's place: tap the map, search, or paste a Google Maps link. */
@Component({
  selector: 'app-place-picker',
  imports: [FormsModule],
  template: `
    <div class="flex flex-col gap-2">
      <div role="tablist" class="tabs tabs-box tabs-sm">
        <button type="button" class="tab" [class.tab-active]="tab() === 'map'" (click)="tab.set('map')">Tap map</button>
        <button type="button" class="tab" [class.tab-active]="tab() === 'search'" (click)="tab.set('search')">Search</button>
        <button type="button" class="tab" [class.tab-active]="tab() === 'link'" (click)="tab.set('link')">Google Maps link</button>
      </div>

      @if (tab() === 'search') {
        <div class="join w-full">
          <input class="input input-sm join-item w-full" placeholder="Cubbon Park, Bengaluru" [(ngModel)]="query" (keydown.enter)="$event.preventDefault(); search()" />
          <button type="button" class="btn btn-sm join-item" (click)="search()">Search</button>
        </div>
        @for (r of results(); track r.place_id) {
          <button type="button" class="btn btn-ghost btn-sm justify-start truncate text-left font-normal" (click)="choose(+r.lat, +r.lon, r.display_name)">
            {{ r.display_name }}
          </button>
        }
      }
      @if (tab() === 'link') {
        <div class="join w-full">
          <input class="input input-sm join-item w-full" placeholder="https://maps.app.goo.gl/…" [(ngModel)]="link" />
          <button type="button" class="btn btn-sm join-item" (click)="importLink()">Import</button>
        </div>
        <p class="text-xs text-base-content/60">In Google Maps: open a place → Share → Copy link, then paste it here.</p>
      }
      @if (info()) {
        <div class="text-xs text-warning">{{ info() }}</div>
      }

      <div #mapEl class="h-64 w-full overflow-hidden rounded-box"></div>
      <div class="flex items-center justify-between text-xs text-base-content/60">
        <span>{{ value() ? (value()!.name ?? value()!.lat.toFixed(5) + ', ' + value()!.lng.toFixed(5)) : 'No place picked yet' }}</span>
        <button type="button" class="btn btn-ghost btn-xs" (click)="useMyLocation()">◎ Use my location</button>
      </div>
    </div>
  `,
})
export class PlacePickerComponent implements OnDestroy {
  value = model<Place | null>(null);
  radius = input(100);

  private http = inject(HttpClient);
  private game = inject(GameService);
  private location = inject(LocationService);
  private mapEl = viewChild.required<ElementRef<HTMLDivElement>>('mapEl');
  private map?: MlMap;
  private marker?: Marker;
  private ready = signal(false);

  protected tab = signal<'map' | 'search' | 'link'>('map');
  protected results = signal<{ place_id: number; lat: string; lon: string; display_name: string }[]>([]);
  protected info = signal<string | null>(null);
  protected query = '';
  protected link = '';

  constructor() {
    afterNextRender(() => {
      const v = this.value();
      const pos = this.location.position();
      this.map = new MlMap({
        container: this.mapEl().nativeElement,
        style: MAP_STYLES['default'] as string,
        center: v ? [v.lng, v.lat] : pos ? [pos.lng, pos.lat] : [77.5946, 12.9716],
        zoom: v || pos ? 15 : 12,
        attributionControl: false,
      });
      this.map.addControl(new AttributionControl({ compact: true }), 'bottom-left');
      this.map.on('load', () => {
        this.map!.addSource('geofence', { type: 'geojson', data: { type: 'FeatureCollection', features: [] } });
        this.map!.addLayer({ id: 'g', type: 'fill', source: 'geofence', paint: { 'fill-color': '#f5b700', 'fill-opacity': 0.15 } });
        this.ready.set(true);
      });
      this.map.on('click', (e) => this.choose(e.lngLat.lat, e.lngLat.lng, null));
    });

    effect(() => {
      const v = this.value();
      const r = this.radius();
      const ready = this.ready();
      if (!this.map || !v) return;
      if (!this.marker) this.marker = new Marker({ color: '#f5b700' }).setLngLat([v.lng, v.lat]).addTo(this.map);
      this.marker.setLngLat([v.lng, v.lat]);
      if (ready) (this.map.getSource('geofence') as GeoJSONSource).setData(circlePolygon(v.lat, v.lng, r));
    });
  }

  ngOnDestroy() {
    this.map?.remove();
  }

  protected choose(lat: number, lng: number, name: string | null) {
    this.info.set(null);
    this.value.set({ lat, lng, name: name ? name.split(',')[0] : null });
    this.map?.easeTo({ center: [lng, lat], zoom: Math.max(this.map.getZoom(), 15) });
  }

  protected search() {
    if (!this.query.trim()) return;
    const pos = this.location.position();
    const params: Record<string, string> = { format: 'json', q: this.query, limit: '6' };
    if (pos) params['viewbox'] = `${pos.lng - 0.3},${pos.lat + 0.3},${pos.lng + 0.3},${pos.lat - 0.3}`;
    this.http.get<{ place_id: number; lat: string; lon: string; display_name: string }[]>('https://nominatim.openstreetmap.org/search', { params }).subscribe({
      next: (r) => {
        this.results.set(r);
        if (!r.length) this.info.set('No results');
      },
      error: () => this.info.set('Search is unavailable right now'),
    });
  }

  protected importLink() {
    this.game.importLink(this.link).subscribe({
      next: (r) => {
        if (r.lat !== null && r.lng !== null) this.choose(r.lat, r.lng, r.place_name);
        else {
          this.tab.set('map');
          this.info.set(`Found "${r.place_name ?? 'a place'}" but no coordinates — tap the map to drop the pin.`);
        }
      },
      error: (e) => this.info.set(errMsg(e)),
    });
  }

  protected useMyLocation() {
    const p = this.location.position();
    if (p) this.choose(p.lat, p.lng, null);
    else this.info.set('Waiting for GPS…');
  }
}
