import { Component, OnInit, computed, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { errMsg, GameService } from '../core/game.service';
import { Category, Story } from '../core/models';
import { Place, PlacePickerComponent } from '../shared/place-picker.component';

interface ChapterDraft {
  title: string;
  category: Category;
  place: Place | null;
  dwell_min: number;
}

@Component({
  selector: 'app-stories',
  imports: [FormsModule, PlacePickerComponent],
  template: `
    <div class="mx-auto max-w-xl p-4 pb-24">
      <h1 class="hud-font mb-1 text-3xl font-bold">Main story</h1>
      <p class="mb-4 text-sm text-base-content/60">A big goal split into chapters. Chapter N+1 unlocks when you finish chapter N. 200 XP each.</p>

      @if (!unlocked()) {
        <div class="alert">🔒 Main story missions unlock at level 3.</div>
      } @else {
        @for (s of stories(); track s.id) {
          <div class="mb-3 rounded-box bg-base-200 p-4">
            <div class="mb-2 flex items-center justify-between">
              <h2 class="hud-font text-xl font-bold text-primary">{{ s.title }}</h2>
              <span class="badge" [class.badge-success]="s.status === 'completed'">{{ s.status }}</span>
            </div>
            <ul class="steps steps-vertical w-full">
              @for (c of s.chapters; track c.id) {
                <li class="step" [class.step-primary]="c.status === 'completed' || c.status === 'active' || c.status === 'in_progress'">
                  <span class="text-left text-sm">
                    Ch {{ c.chapter_no }}: {{ c.title }}
                    @if (c.status === 'draft') { <span class="text-base-content/40">🔒</span> }
                    @if (c.status === 'completed') { <span class="text-success">✓</span> }
                  </span>
                </li>
              }
            </ul>
          </div>
        } @empty {
          <div class="mb-4 text-sm text-base-content/60">No stories yet.</div>
        }

        <details class="collapse-arrow collapse bg-base-200" [open]="!stories().length">
          <summary class="collapse-title hud-font text-lg">+ New story</summary>
          <div class="collapse-content flex flex-col gap-3">
            <input class="input w-full" placeholder="Get fit" [(ngModel)]="title" />
            @for (ch of chapters; track $index; let i = $index) {
              <div class="rounded-box border border-base-300 p-3">
                <div class="mb-2 flex items-center gap-2">
                  <span class="hud-font font-bold">Chapter {{ i + 1 }}</span>
                  @if (chapters.length > 1) {
                    <button type="button" class="btn btn-ghost btn-xs ml-auto" (click)="chapters.splice(i, 1)">Remove</button>
                  }
                </div>
                <input class="input input-sm mb-2 w-full" placeholder="Gym ×5" [(ngModel)]="ch.title" />
                <div class="mb-2 flex gap-2">
                  <select class="select select-sm flex-1" [(ngModel)]="ch.category">
                    <option value="fitness">Fitness</option>
                    <option value="explore">Explore</option>
                    <option value="social">Social</option>
                    <option value="habit">Habit</option>
                  </select>
                  <input type="number" min="0" class="input input-sm w-28" placeholder="Stay (min)" [(ngModel)]="ch.dwell_min" />
                </div>
                <p class="mb-2 text-xs text-base-content/50">No place = a home chapter you confirm yourself.</p>
                @if (editing === i) {
                  @if (ch.place) {
                    <button type="button" class="btn btn-ghost btn-xs mb-1" (click)="ch.place = null">Clear place</button>
                  }
                  <app-place-picker [(value)]="ch.place" />
                } @else {
                  <button type="button" class="btn btn-xs" (click)="editing = i">
                    {{ ch.place ? '📍 ' + (ch.place.name ?? 'Place set') + ' — change' : 'Pick a place' }}
                  </button>
                }
              </div>
            }
            <button type="button" class="btn btn-sm btn-ghost" (click)="addChapter()">+ Add chapter</button>
            @if (error()) {
              <div class="alert alert-error text-sm">{{ error() }}</div>
            }
            <button class="btn btn-primary" (click)="create()" [disabled]="busy()">Start story</button>
          </div>
        </details>
      }
    </div>
  `,
})
export class StoriesPage implements OnInit {
  private game = inject(GameService);
  protected stories = signal<Story[]>([]);
  protected busy = signal(false);
  protected error = signal<string | null>(null);
  protected unlocked = computed(() => this.game.me()?.unlocks?.['mission_type:main'] === 'true');
  protected title = '';
  protected editing = 0;
  protected chapters: ChapterDraft[] = [{ title: '', category: 'fitness', place: null, dwell_min: 0 }];

  ngOnInit() {
    this.load();
  }

  load() {
    this.game.stories().subscribe((s) => this.stories.set(s));
  }

  addChapter() {
    this.chapters.push({ title: '', category: 'fitness', place: null, dwell_min: 0 });
    this.editing = this.chapters.length - 1;
  }

  create() {
    this.error.set(null);
    if (!this.title.trim() || this.chapters.some((c) => !c.title.trim())) {
      this.error.set('Give the story and every chapter a title.');
      return;
    }
    this.busy.set(true);
    const chapters = this.chapters.map((c) => ({
      title: c.title,
      category: c.category,
      dwell_min: c.dwell_min || 0,
      lat: c.place?.lat ?? null,
      lng: c.place?.lng ?? null,
      place_name: c.place?.name ?? null,
    }));
    this.game.createStory(this.title, chapters).subscribe({
      next: () => {
        this.busy.set(false);
        this.title = '';
        this.chapters = [{ title: '', category: 'fitness', place: null, dwell_min: 0 }];
        this.game.loadMe().subscribe();
        this.load();
      },
      error: (e) => {
        this.busy.set(false);
        this.error.set(errMsg(e));
      },
    });
  }
}
