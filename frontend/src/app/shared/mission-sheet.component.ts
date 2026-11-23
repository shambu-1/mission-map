import { DatePipe } from '@angular/common';
import { Component, computed, inject, input, output, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { RouterLink } from '@angular/router';
import { errMsg, GameService } from '../core/game.service';
import { LocationService } from '../core/location.service';
import { formatDistance, isNight } from '../core/map-utils';
import { Mission, TYPE_ICON, TYPE_LABEL } from '../core/models';

/** Mission detail with "Set waypoint", check-in, proof upload, abandon. */
@Component({
  selector: 'app-mission-sheet',
  imports: [DatePipe, FormsModule, RouterLink],
  template: `
    @let m = mission();
    <div class="rounded-t-box bg-base-200 p-4 shadow-2xl">
      <div class="mb-2 flex items-start gap-2">
        <span class="badge" [class]="badgeClass()">{{ icon[m.type] }} {{ label[m.type] }}</span>
        @if (m.sponsored) {
          <span class="badge badge-warning badge-outline">Sponsored</span>
        }
        @if (m.chapter_no) {
          <span class="badge badge-ghost">Chapter {{ m.chapter_no }}</span>
        }
        <button class="btn btn-ghost btn-sm btn-circle ml-auto" (click)="closed.emit()" aria-label="Close">✕</button>
      </div>
      <h2 class="hud-font text-2xl font-bold">{{ m.title }}</h2>
      @if (m.place_name) {
        <div class="text-sm text-base-content/60">{{ m.place_name }}</div>
      }
      @if (m.description) {
        <p class="mt-2 text-sm">{{ m.description }}</p>
      }

      <div class="mt-3 flex flex-wrap gap-2 text-xs">
        @if (m.lat !== null) {
          <span class="badge badge-ghost">📍 {{ distanceText() }}</span>
          <span class="badge badge-ghost">⭕ {{ m.radius_m }} m geofence</span>
        }
        @if (m.dwell_min) {
          <span class="badge badge-ghost">⏳ stay {{ m.dwell_min }} min</span>
        }
        @if (m.proof_required) {
          <span class="badge badge-ghost">📷 proof {{ m.has_proof ? '✓' : 'needed' }}</span>
        }
        @if (m.deadline) {
          <span class="badge badge-error badge-outline">⏱ {{ m.deadline | date: 'EEE d MMM, HH:mm' }}</span>
        }
        @if (m.status === 'in_progress') {
          <span class="badge badge-info">In progress</span>
        }
      </div>

      @if (m.visibility === 'public' && night()) {
        <div class="alert alert-warning mt-3 py-2 text-xs">It's late (22:00–06:00). Only go if the place is safe and well lit.</div>
      }
      @if (location.drivingLock()) {
        <div class="alert alert-error mt-3 py-2 text-sm">Moving over 20 km/h — don't play while driving.</div>
      }
      @if (message()) {
        <div class="alert mt-3 py-2 text-sm" [class.alert-error]="msgKind() === 'error'" [class.alert-info]="msgKind() === 'info'">
          {{ message() }}
        </div>
      }

      <div class="mt-4 grid grid-cols-2 gap-2">
        @if (m.waypoint_url) {
          <a class="btn btn-secondary" [href]="m.waypoint_url" target="_blank" rel="noopener">➤ Set waypoint</a>
        }
        @if (isMine() || m.squad_id) {
          @if (m.status === 'active' || m.status === 'in_progress') {
            <button class="btn btn-primary" [class.col-span-2]="!m.waypoint_url" [disabled]="busy() || location.drivingLock()" (click)="checkin()">
              {{ m.lat === null ? 'Mark done' : 'Check in' }}
            </button>
          }
        } @else if (m.visibility === 'public') {
          <button class="btn btn-primary" (click)="take()" [disabled]="busy()">Take mission</button>
        }
      </div>

      @if (isMine() && (m.status === 'active' || m.status === 'in_progress')) {
        <details class="collapse-arrow collapse mt-3 bg-base-300">
          <summary class="collapse-title text-sm font-medium">Proof & more</summary>
          <div class="collapse-content flex flex-col gap-2">
            <input type="file" accept="image/jpeg,image/png,image/webp" class="file-input file-input-sm w-full" (change)="onFile($event)" />
            <input class="input input-sm w-full" placeholder="Short note (optional)" [(ngModel)]="note" />
            <button class="btn btn-sm" (click)="uploadProof()" [disabled]="busy() || (!file && !note)">Upload proof</button>
            @if (m.has_proof && m.lat !== null) {
              <button class="btn btn-sm btn-outline" (click)="checkin(true)" [disabled]="busy()">GPS weak? "I'm here" with proof</button>
            }
            <div class="mt-2 flex gap-2">
              @if (m.source === 'self' && !m.squad_id) {
                <a class="btn btn-sm btn-ghost flex-1" [routerLink]="['/missions', m.id, 'edit']">Edit</a>
              }
              <button class="btn btn-sm btn-ghost flex-1 text-error" (click)="abandon()">Abandon</button>
            </div>
          </div>
        </details>
      }
      @if (!isMine() && m.visibility === 'public') {
        <button class="btn btn-ghost btn-xs mt-3 text-error" (click)="report()">Report this mission</button>
      }
    </div>
  `,
})
export class MissionSheetComponent {
  mission = input.required<Mission>();
  closed = output<void>();
  changed = output<void>();

  protected game = inject(GameService);
  protected location = inject(LocationService);
  protected label = TYPE_LABEL;
  protected icon = TYPE_ICON;
  protected busy = signal(false);
  protected message = signal<string | null>(null);
  protected msgKind = signal<'error' | 'info'>('info');
  protected night = signal(isNight());
  protected file: File | null = null;
  protected note = '';

  protected isMine = computed(() => this.mission().owner_id === this.game.me()?.id);
  protected distanceText = computed(() => {
    const m = this.mission();
    const live = m.lat !== null && m.lng !== null ? this.location.distanceTo(m.lat, m.lng) : null;
    return formatDistance(live ?? m.distance_m);
  });
  protected badgeClass = computed(() => {
    const t = this.mission().type;
    return { main: 'badge-primary', side: 'badge-secondary', daily: 'badge-accent', timed: 'badge-error', home: 'badge-ghost' }[t];
  });

  protected onFile(e: Event) {
    this.file = (e.target as HTMLInputElement).files?.[0] ?? null;
  }

  checkin(manual = false) {
    const m = this.mission();
    const pos = this.location.position();
    if (m.lat !== null && !pos) {
      this.show('Waiting for GPS… allow location access and try again.', 'error');
      return;
    }
    const body =
      m.lat === null
        ? {}
        : { lat: pos!.lat, lng: pos!.lng, accuracy_m: pos!.accuracy, timestamp: new Date(pos!.timestamp).toISOString(), manual };
    this.busy.set(true);
    this.game.checkin(m.id, body).subscribe({
      next: (r) => {
        this.busy.set(false);
        if (r.status === 'completed') {
          this.game.passed.set({ ...r, title: m.title });
          this.game.loadMe().subscribe();
          this.changed.emit();
          this.closed.emit();
        } else if (r.status === 'in_progress') {
          this.show(r.reason ?? `Stay here — ${r.dwell_remaining_min} min to go. Check in again when the time's up.`, 'info');
          this.changed.emit();
        } else {
          this.show(r.reason ?? 'Check-in rejected', 'error');
        }
      },
      error: (e) => {
        this.busy.set(false);
        this.show(errMsg(e), 'error');
      },
    });
  }

  uploadProof() {
    this.busy.set(true);
    this.game.uploadProof(this.mission().id, this.file, this.note || null).subscribe({
      next: () => {
        this.busy.set(false);
        this.show('Proof saved (photo location data stripped).', 'info');
        this.changed.emit();
      },
      error: (e) => {
        this.busy.set(false);
        this.show(errMsg(e), 'error');
      },
    });
  }

  take() {
    this.busy.set(true);
    this.game.take(this.mission().id).subscribe({
      next: () => {
        this.busy.set(false);
        this.show('Added to your missions.', 'info');
        this.changed.emit();
      },
      error: (e) => {
        this.busy.set(false);
        this.show(errMsg(e), 'error');
      },
    });
  }

  abandon() {
    if (!confirm('Abandon this mission?')) return;
    this.game.abandon(this.mission().id).subscribe(() => {
      this.game.loadMe().subscribe();
      this.changed.emit();
      this.closed.emit();
    });
  }

  report() {
    const reason = prompt('What is wrong with this mission?');
    if (reason && reason.length >= 3) this.game.report(this.mission().id, reason).subscribe(() => this.show('Reported. Thanks!', 'info'));
  }

  private show(text: string, kind: 'error' | 'info') {
    this.message.set(text);
    this.msgKind.set(kind);
  }
}
