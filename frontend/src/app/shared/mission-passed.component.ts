import { Component, effect, inject } from '@angular/core';
import { GameService } from '../core/game.service';

/** GTA-style "MISSION PASSED" overlay with XP, cash and any new unlocks. */
@Component({
  selector: 'app-mission-passed',
  template: `
    @if (game.passed(); as p) {
      <div
        class="fixed inset-0 z-[1000] flex flex-col items-center justify-center gap-3 bg-black/75 p-6 text-center"
        (click)="game.passed.set(null)"
      >
        <div class="mission-passed-title">MISSION PASSED</div>
        <div class="hud-font text-xl text-white/90">{{ p.title }}</div>
        <div class="hud-font text-3xl font-bold text-success">+{{ p.xp_awarded }} XP</div>
        @if (p.cash_awarded) {
          <div class="hud-font text-xl text-success">+\${{ p.cash_awarded }}</div>
        }
        @if (p.streak_days && p.streak_days > 1) {
          <div class="badge badge-warning badge-lg">🔥 {{ p.streak_days }}-day streak</div>
        }
        @if (p.level_up) {
          <div class="hud-font mt-2 text-4xl font-bold text-primary">LEVEL {{ p.level }}</div>
        }
        @for (u of p.new_unlocks; track u.feature_key) {
          <div class="badge badge-primary badge-lg">Unlocked: {{ u.label }}</div>
        }
        @if (p.district_claimed) {
          <div class="badge badge-secondary badge-lg">District claimed: {{ p.district_claimed }}</div>
        }
        @if (p.reason) {
          <div class="text-sm text-warning">{{ p.reason }}</div>
        }
        <div class="mt-4 text-xs text-white/50">Tap to continue</div>
      </div>
    }
  `,
})
export class MissionPassedComponent {
  protected game = inject(GameService);

  constructor() {
    // Play a short sound (no audio files needed) and auto-hide after a while
    effect(() => {
      if (!this.game.passed()) return;
      this.beep();
      setTimeout(() => this.game.passed.set(null), 6000);
    });
  }

  private beep() {
    try {
      const ctx = new AudioContext();
      [523, 659, 784, 1046].forEach((freq, i) => {
        const o = ctx.createOscillator();
        const g = ctx.createGain();
        o.frequency.value = freq;
        o.type = 'triangle';
        g.gain.setValueAtTime(0.15, ctx.currentTime + i * 0.12);
        g.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + i * 0.12 + 0.3);
        o.connect(g).connect(ctx.destination);
        o.start(ctx.currentTime + i * 0.12);
        o.stop(ctx.currentTime + i * 0.12 + 0.3);
      });
    } catch {
      /* audio not available */
    }
  }
}
