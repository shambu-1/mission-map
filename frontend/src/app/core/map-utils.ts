import type { Feature, FeatureCollection, Polygon } from 'geojson';
import type { StyleSpecification } from 'maplibre-gl';

/** Free vector styles from OpenFreeMap (OpenStreetMap data, no API key). */
export const MAP_STYLES: Record<string, string | StyleSpecification> = {
  default: 'https://tiles.openfreemap.org/styles/liberty',
  night: 'https://tiles.openfreemap.org/styles/dark',
  retro: 'https://tiles.openfreemap.org/styles/positron',
  neon: 'https://tiles.openfreemap.org/styles/dark',
};

/** Raster OSM fallback, used if the vector style can't load. */
export const RASTER_FALLBACK: StyleSpecification = {
  version: 8,
  sources: {
    osm: {
      type: 'raster',
      tiles: ['https://tile.openstreetmap.org/{z}/{x}/{y}.png'],
      tileSize: 256,
      attribution: '© OpenStreetMap contributors',
    },
  },
  layers: [{ id: 'osm', type: 'raster', source: 'osm' }],
};

/** Geofence circle as a GeoJSON polygon. */
export function circlePolygon(lat: number, lng: number, radiusM: number, steps = 64): Feature<Polygon> {
  const coords: [number, number][] = [];
  const dLat = radiusM / 111320;
  const dLng = radiusM / (111320 * Math.cos((lat * Math.PI) / 180));
  for (let i = 0; i <= steps; i++) {
    const t = (i / steps) * 2 * Math.PI;
    coords.push([lng + dLng * Math.cos(t), lat + dLat * Math.sin(t)]);
  }
  return { type: 'Feature', properties: {}, geometry: { type: 'Polygon', coordinates: [coords] } };
}

/** Fog of war: a dark polygon over the visible area with every explored hexagon cut out as a hole. */
export function fogPolygon(
  bounds: [number, number, number, number],
  explored: FeatureCollection<Polygon>,
): Feature<Polygon> {
  const [w, s, e, n] = bounds;
  const pad = Math.max(e - w, n - s);
  const outer: [number, number][] = [
    [w - pad, s - pad],
    [e + pad, s - pad],
    [e + pad, n + pad],
    [w - pad, n + pad],
    [w - pad, s - pad],
  ];
  const holes = explored.features.map((f) => [...f.geometry.coordinates[0]].reverse() as [number, number][]);
  return { type: 'Feature', properties: {}, geometry: { type: 'Polygon', coordinates: [outer, ...holes] } };
}

export function formatDistance(m: number | null | undefined): string {
  if (m == null) return '—';
  return m < 1000 ? `${Math.round(m)} m` : `${(m / 1000).toFixed(1)} km`;
}

export function isNight(d = new Date()): boolean {
  const h = d.getHours();
  return h >= 22 || h < 6;
}
