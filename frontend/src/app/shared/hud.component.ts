import { Component, computed, inject } from '@angular/core';
import { RouterLink } from '@angular/router';
import { GameService } from '../core/game.service';

/** Top HUD: level, XP bar, cash, streak, slacker level (wanted stars in reverse), slots. */
@Component({
  selector: 'app-hud',
  imports: [RouterLink],
  template: `
    @if (game.me(); as me) {
      <a routerLink="/profile" class="hud-font flex items-center gap-3 rounded-box bg-base-100/85 px-3 py-2 shadow-lg backdrop-blur">
        <div class="radial-progress text-primary text-sm font-bold" [style.--value]="pct()" style="--size:2.6rem;--thickness:4px" role="progressbar">
          {{ me.level }}
        </div>
        <div class="flex min-w-0 flex-col leading-tight">
          <div class="flex items-center gap-3 text-sm">
            <span class="font-bold text-success">\${{ me.cash }}</span>
            @if (me.streak_days > 0) {
              <span class="text-warning">🔥{{ me.streak_days }} ×{{ me.streak_multiplier }}</span>
            }
            <span class="text-base-content/60">{{ me.slots_used }}/{{ me.slots_total }} slots</span>
          </div>
          <div class="text-xs text-base-content/60">{{ me.xp_into_level }} / {{ me.xp_for_next }} XP</div>
        </div>
        <div class="stars ml-auto text-sm" [title]="'Slacker level ' + me.slacker_stars">
          @for (s of starSlots; track $index) {
            <span [class.off]="$index >= me.slacker_stars">★</span>
          }
        </div>
      </a>
      @if (me.xp_frozen) {
        <div class="alert alert-warning mt-2 py-1 text-xs">XP frozen — your recent check-ins are under review.</div>
      }
    }
  `,
})
export class HudComponent {
  protected game = inject(GameService);
  protected starSlots = new Array(5);
  protected pct = computed(() => {
    const me = this.game.me();
    return me ? Math.round((me.xp_into_level / me.xp_for_next) * 100) : 0;
  });
}
