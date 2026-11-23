import { DatePipe } from '@angular/common';
import { Component, OnInit, computed, inject, signal } from '@angular/core';
import { RouterLink } from '@angular/router';
import { GameService } from '../core/game.service';
import { formatDistance } from '../core/map-utils';
import { Mission, TYPE_ICON, TYPE_LABEL } from '../core/models';
import { MissionSheetComponent } from '../shared/mission-sheet.component';

@Component({
  selector: 'app-missions',
  imports: [DatePipe, RouterLink, MissionSheetComponent],
  template: `
    <div class="mx-auto max-w-xl p-4 pb-24">
      <div class="mb-4 flex items-center justify-between">
        <h1 class="hud-font text-3xl font-bold">Missions</h1>
        <a routerLink="/missions/new" class="btn btn-primary btn-sm">+ New</a>
      </div>

      @if (inbox().length) {
        <h2 class="hud-font mb-2 text-lg text-warning">Inbox — from friends</h2>
        @for (m of inbox(); track m.id) {
          <div class="mb-2 flex items-center gap-2 rounded-box bg-base-200 p-3">
            <div class="flex-1">
              <div class="font-medium">{{ m.title }}</div>
              <div class="text-xs text-base-content/60">{{ m.place_name ?? 'Home mission' }}</div>
            </div>
            <button class="btn btn-success btn-sm" (click)="answer(m, true)">Accept</button>
            <button class="btn btn-ghost btn-sm" (click)="answer(m, false)">Decline</button>
          </div>
        }
      }

      @if (toVerify().length) {
        <h2 class="hud-font mb-2 mt-4 text-lg text-info">Friends waiting for your OK</h2>
        @for (m of toVerify(); track m.id) {
          <div class="mb-2 flex items-center gap-2 rounded-box bg-base-200 p-3">
            <div class="flex-1 font-medium">{{ m.title }}</div>
            <button class="btn btn-info btn-sm" (click)="verify(m)">Verify</button>
          </div>
        }
      }

      <h2 class="hud-font mb-2 mt-4 text-lg">Active</h2>
      @for (m of active(); track m.id) {
        <button class="mb-2 flex w-full items-center gap-3 rounded-box bg-base-200 p-3 text-left hover:bg-base-300" (click)="selected.set(m)">
          <span class="mm-marker !h-8 !w-8 shrink-0 !text-sm" [class]="m.type">{{ icon[m.type] }}</span>
          <div class="min-w-0 flex-1">
            <div class="truncate font-medium">{{ m.title }}</div>
            <div class="text-xs text-base-content/60">
              {{ label[m.type] }}
              @if (m.lat !== null) { · {{ fmt(m.distance_m) }} }
              @if (m.deadline) { · due {{ m.deadline | date: 'd MMM HH:mm' }} }
              @if (m.sponsored) { · Sponsored }
            </div>
          </div>
          @if (m.status === 'in_progress') {
            <span class="badge badge-info badge-sm">in progress</span>
          }
        </button>
      } @empty {
        <div class="rounded-box bg-base-200 p-6 text-center text-sm text-base-content/60">No active missions. Pin one on the map!</div>
      }

      <h2 class="hud-font mb-2 mt-6 text-lg">Recently completed</h2>
      @for (h of history(); track h.mission_id + h.completed_at) {
        <div class="flex items-center justify-between border-b border-base-300 py-2 text-sm">
          <span class="truncate">{{ h.title }}</span>
          <span class="shrink-0 text-success">+{{ h.xp_awarded }} XP</span>
        </div>
      } @empty {
        <div class="text-sm text-base-content/60">Nothing yet.</div>
      }
    </div>

    @if (selected(); as m) {
      <div class="fixed inset-x-0 bottom-16 z-40 mx-auto max-h-[75%] max-w-xl overflow-y-auto">
        <app-mission-sheet [mission]="m" (closed)="selected.set(null)" (changed)="load()" />
      </div>
    }
  `,
})
export class MissionsPage implements OnInit {
  protected game = inject(GameService);
  protected label = TYPE_LABEL;
  protected icon = TYPE_ICON;
  protected fmt = formatDistance;

  protected selected = signal<Mission | null>(null);
  protected inbox = signal<Mission[]>([]);
  protected toVerify = signal<Mission[]>([]);
  protected history = signal<{ mission_id: number; title: string; xp_awarded: number; completed_at: string }[]>([]);
  protected active = computed(() =>
    this.game.missions().filter((m) => m.owner_id === this.game.me()?.id && ['active', 'in_progress'].includes(m.status)),
  );

  ngOnInit() {
    this.load();
  }

  load() {
    this.game.loadMissions().subscribe();
    this.game.inbox().subscribe((r) => this.inbox.set(r));
    this.game.toVerify().subscribe((r) => this.toVerify.set(r));
    this.game.history().subscribe((r) => this.history.set(r.slice(0, 15)));
  }

  answer(m: Mission, accept: boolean) {
    this.game.answerInbox(m.id, accept).subscribe({ next: () => this.load(), error: (e) => alert(e.error?.detail ?? 'Failed') });
  }

  verify(m: Mission) {
    this.game.verify(m.id).subscribe({ next: () => this.load(), error: (e) => alert(e.error?.detail ?? 'Failed') });
  }
}
