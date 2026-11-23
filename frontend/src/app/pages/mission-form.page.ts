import { Component, OnInit, computed, inject, input, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { ActivatedRoute, Router } from '@angular/router';
import { errMsg, GameService } from '../core/game.service';
import { Category, MissionInput, MissionType } from '../core/models';
import { Place, PlacePickerComponent } from '../shared/place-picker.component';

@Component({
  selector: 'app-mission-form',
  imports: [FormsModule, PlacePickerComponent],
  template: `
    <div class="mx-auto max-w-xl p-4">
      <h1 class="hud-font mb-4 text-3xl font-bold">{{ id() ? 'Edit mission' : 'New mission' }}</h1>

      <form class="flex flex-col gap-4" (ngSubmit)="save()">
        <input class="input w-full" name="title" placeholder="Try the new biryani place" maxlength="120" [(ngModel)]="form.title" required />
        <textarea class="textarea w-full" name="description" placeholder="Notes (optional)" [(ngModel)]="form.description"></textarea>

        @if (!id()) {
          <div>
            <div class="mb-1 text-sm font-medium">Type</div>
            <div class="flex flex-wrap gap-2">
              @for (t of types; track t.value) {
                <button
                  type="button"
                  class="btn btn-sm"
                  [class.btn-primary]="form.type === t.value"
                  [disabled]="!allowed(t.value)"
                  (click)="form.type = t.value"
                  [title]="allowed(t.value) ? t.hint : 'Locked at your level'"
                >
                  {{ t.label }}{{ allowed(t.value) ? '' : ' 🔒' }}
                </button>
              }
            </div>
            <p class="mt-1 text-xs text-base-content/60">{{ hint() }}</p>
          </div>
        }

        <label class="flex flex-col gap-1">
          <span class="text-sm font-medium">Category (feeds a stat)</span>
          <select class="select w-full" name="category" [(ngModel)]="form.category">
            <option value="fitness">Fitness → Stamina</option>
            <option value="explore">Explore → Explorer</option>
            <option value="social">Social → Social</option>
            <option value="habit">Habit</option>
          </select>
        </label>

        @if (form.type !== 'home') {
          <app-place-picker [(value)]="place" [radius]="form.radius_m" />

          <label class="flex flex-col gap-1">
            <span class="text-sm font-medium">Geofence radius: {{ form.radius_m }} m</span>
            <input type="range" min="50" max="500" step="10" class="range range-sm range-primary" name="radius" [(ngModel)]="form.radius_m" />
          </label>
          <label class="flex flex-col gap-1">
            <span class="text-sm font-medium">Stay for (minutes, 0 = just arrive)</span>
            <input type="number" min="0" max="240" class="input w-full" name="dwell" [(ngModel)]="form.dwell_min" />
          </label>
        }

        <label class="label cursor-pointer justify-start gap-3">
          <input type="checkbox" class="toggle toggle-primary" name="proof" [(ngModel)]="form.proof_required" />
          <span>Require photo or note as proof</span>
        </label>

        @if (form.type === 'timed' || form.deadline) {
          <label class="flex flex-col gap-1">
            <span class="text-sm font-medium">Deadline (+50% XP if done in time)</span>
            <input type="datetime-local" class="input w-full" name="deadline" [(ngModel)]="deadlineLocal" />
          </label>
        }

        <label class="flex flex-col gap-1">
          <span class="text-sm font-medium">Who can see it</span>
          <select class="select w-full" name="visibility" [(ngModel)]="form.visibility">
            <option value="private">Only me</option>
            <option value="friends">Friends</option>
            <option value="public" [disabled]="!publicUnlocked()">Public (level 20+, reviewed)</option>
          </select>
        </label>

        @if (error()) {
          <div class="alert alert-error text-sm">{{ error() }}</div>
        }
        <div class="flex gap-2">
          <button type="button" class="btn btn-ghost flex-1" (click)="router.navigateByUrl('/')">Cancel</button>
          <button class="btn btn-primary hud-font flex-1 text-lg" [disabled]="busy()">{{ id() ? 'SAVE' : 'START MISSION' }}</button>
        </div>
      </form>
    </div>
  `,
})
export class MissionFormPage implements OnInit {
  id = input<string>();

  protected game = inject(GameService);
  protected router = inject(Router);
  private route = inject(ActivatedRoute);

  protected busy = signal(false);
  protected error = signal<string | null>(null);
  protected place: Place | null = null;
  protected deadlineLocal = '';
  protected form: MissionInput = {
    title: '',
    description: '',
    type: 'side',
    category: 'explore',
    radius_m: 100,
    dwell_min: 0,
    proof_required: false,
    visibility: 'private',
  };

  protected types: { value: MissionType; label: string; hint: string }[] = [
    { value: 'side', label: 'Side', hint: 'One-off task at a place — 50 XP.' },
    { value: 'timed', label: 'Timed', hint: 'Has a deadline — +50% XP if done in time.' },
    { value: 'home', label: 'Home', hint: 'No location, self-confirmed — 15 XP.' },
  ];

  protected publicUnlocked = computed(() => this.game.me()?.unlocks?.['public_missions'] === 'true');

  ngOnInit() {
    const q = this.route.snapshot.queryParamMap;
    if (q.get('lat') && q.get('lng')) this.place = { lat: +q.get('lat')!, lng: +q.get('lng')!, name: null };
    const id = this.id();
    if (id) {
      this.game.mission(+id).subscribe((m) => {
        this.form = {
          title: m.title,
          description: m.description,
          type: m.type,
          category: m.category as Category,
          radius_m: m.radius_m,
          dwell_min: m.dwell_min,
          proof_required: m.proof_required,
          deadline: m.deadline,
          visibility: m.visibility,
        };
        if (m.lat !== null) this.place = { lat: m.lat, lng: m.lng!, name: m.place_name };
        if (m.deadline) this.deadlineLocal = toLocalInput(m.deadline);
      });
    }
  }

  protected allowed(t: MissionType) {
    const u = this.game.me()?.unlocks ?? {};
    return u[`mission_type:${t}`] === 'true';
  }

  protected hint() {
    return this.types.find((t) => t.value === this.form.type)?.hint ?? '';
  }

  protected save() {
    this.error.set(null);
    if (this.form.type !== 'home' && !this.place) {
      this.error.set('Pick a place: tap the map, search, or paste a Google Maps link.');
      return;
    }
    const body: MissionInput = {
      ...this.form,
      lat: this.form.type === 'home' ? null : this.place!.lat,
      lng: this.form.type === 'home' ? null : this.place!.lng,
      place_name: this.form.type === 'home' ? null : this.place!.name,
      deadline: this.deadlineLocal ? new Date(this.deadlineLocal).toISOString() : null,
    };
    this.busy.set(true);
    const id = this.id();
    const req = id ? this.game.update(+id, body) : this.game.create(body);
    req.subscribe({
      next: () => {
        this.game.loadMe().subscribe();
        this.router.navigateByUrl('/');
      },
      error: (e) => {
        this.error.set(errMsg(e));
        this.busy.set(false);
      },
    });
  }
}

function toLocalInput(iso: string): string {
  const d = new Date(iso);
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}
