import { Component, OnInit, computed, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { AuthService } from '../core/auth.service';
import { errMsg, GameService } from '../core/game.service';
import { TravelMode } from '../core/models';

/** GTA-style stats screen + settings + shop. */
@Component({
  selector: 'app-profile',
  imports: [FormsModule],
  template: `
    @if (game.me(); as me) {
      <div class="mx-auto max-w-xl p-4 pb-24">
        <div class="mb-4 flex items-end justify-between">
          <div>
            <h1 class="hud-font text-3xl font-bold">{{ me.username }}</h1>
            <div class="text-sm text-base-content/60">{{ me.completed_missions }} missions · {{ me.explored_cells }} hexes explored</div>
          </div>
          <div class="hud-font text-right">
            <div class="text-4xl font-bold text-primary">LV {{ me.level }}</div>
            <div class="text-success">\${{ me.cash }}</div>
          </div>
        </div>

        <progress class="progress progress-primary w-full" [value]="me.xp_into_level" [max]="me.xp_for_next"></progress>
        <div class="mb-4 text-right text-xs text-base-content/60">{{ me.xp_into_level }} / {{ me.xp_for_next }} XP to level {{ me.level + 1 }}</div>

        <h2 class="hud-font mb-2 text-xl">Stats</h2>
        <div class="mb-6 flex flex-col gap-2 rounded-box bg-base-200 p-4">
          @for (s of stats(); track s.name) {
            <div>
              <div class="flex justify-between text-sm"><span>{{ s.name }}</span><span class="text-base-content/60">{{ s.value }}</span></div>
              <progress class="progress w-full" [class]="s.cls" [value]="s.value" [max]="s.max"></progress>
              <div class="text-xs text-base-content/40">{{ s.hint }}</div>
            </div>
          }
        </div>

        <h2 class="hud-font mb-2 text-xl">Streak & slacker level</h2>
        <div class="mb-6 grid grid-cols-2 gap-2">
          <div class="rounded-box bg-base-200 p-4 text-center">
            <div class="hud-font text-3xl text-warning">🔥 {{ me.streak_days }}</div>
            <div class="text-xs text-base-content/60">day streak · XP ×{{ me.streak_multiplier }}</div>
          </div>
          <div class="rounded-box bg-base-200 p-4 text-center">
            <div class="stars text-2xl">
              @for (s of starSlots; track $index) {
                <span [class.off]="$index >= me.slacker_stars">★</span>
              }
            </div>
            <div class="text-xs text-base-content/60">slacker level — finish any mission to clear</div>
          </div>
        </div>

        <h2 class="hud-font mb-2 text-xl">Unlocks</h2>
        <ul class="mb-6 rounded-box bg-base-200 p-2">
          @for (u of unlockTable(); track u.level + u.feature_key) {
            <li class="flex items-center gap-3 px-2 py-1 text-sm" [class.opacity-40]="u.level > me.level">
              <span class="hud-font w-10 font-bold">Lv {{ u.level }}</span>
              <span class="flex-1">{{ u.label }}</span>
              <span>{{ u.level <= me.level ? '✓' : '🔒' }}</span>
            </li>
          }
        </ul>

        <h2 class="hud-font mb-2 text-xl">Settings</h2>
        <div class="mb-6 flex flex-col gap-3 rounded-box bg-base-200 p-4">
          <label class="flex flex-col gap-1">
            <span class="text-sm">Default travel mode for "Set waypoint"</span>
            <select class="select w-full" [ngModel]="me.travel_mode" (ngModelChange)="setMode($event)">
              <option value="driving">Driving</option>
              <option value="two-wheeler">Two-wheeler</option>
              <option value="walking">Walking</option>
              <option value="transit">Transit</option>
            </select>
          </label>
          <label class="flex flex-col gap-1">
            <span class="text-sm">Map skin</span>
            <select class="select w-full" [ngModel]="me.map_skin" (ngModelChange)="setSkin($event)">
              <option value="default">Default</option>
              @for (s of ['night', 'retro', 'neon']; track s) {
                <option [value]="s" [disabled]="!ownsSkin(s)">{{ s }}{{ ownsSkin(s) ? '' : ' (shop)' }}</option>
              }
            </select>
          </label>
        </div>

        <h2 class="hud-font mb-2 text-xl">Shop</h2>
        <div class="mb-6 grid grid-cols-2 gap-2">
          @for (item of shop(); track item.item) {
            <button class="btn h-auto flex-col py-2" [disabled]="item.owned || me.level < item.level || me.cash < item.price" (click)="buy(item.item)">
              <span>{{ itemName(item.item) }}</span>
              <span class="text-xs text-success">
                {{ item.owned ? 'Owned' : me.level < item.level ? 'Lv ' + item.level : '$' + item.price }}
              </span>
            </button>
          }
        </div>
        @if (msg()) {
          <div class="alert mb-4 text-sm">{{ msg() }}</div>
        }

        <div class="flex flex-col gap-2">
          <button class="btn btn-outline" (click)="auth.logout()">Log out</button>
          <button class="btn btn-ghost btn-sm text-error" (click)="deleteAccount()">Delete account and all location data</button>
        </div>
      </div>
    }
  `,
})
export class ProfilePage implements OnInit {
  protected game = inject(GameService);
  protected auth = inject(AuthService);
  protected starSlots = new Array(5);
  protected unlockTable = signal<{ level: number; feature_key: string; label: string }[]>([]);
  protected shop = signal<{ item: string; price: number; level: number; owned: boolean }[]>([]);
  protected msg = signal<string | null>(null);

  protected stats = computed(() => {
    const s = this.game.me()?.stats;
    if (!s) return [];
    const max = Math.max(10, s.stamina, s.explorer, s.social, s.discipline);
    return [
      { name: 'Stamina', value: s.stamina, max, cls: 'progress-error', hint: 'Fitness missions' },
      { name: 'Explorer', value: s.explorer, max, cls: 'progress-accent', hint: 'New places and hexes' },
      { name: 'Social', value: s.social, max, cls: 'progress-secondary', hint: 'Co-op missions' },
      { name: 'Discipline', value: s.discipline, max, cls: 'progress-primary', hint: 'Longest streak' },
    ];
  });

  ngOnInit() {
    this.game.loadMe().subscribe();
    this.game.unlockTable().subscribe((u) => this.unlockTable.set(u.filter((x) => !x.feature_key.startsWith('mission_type:side'))));
    this.game.shop().subscribe((s) => this.shop.set(s));
  }

  setMode(mode: TravelMode) {
    this.game.updateMe({ travel_mode: mode }).subscribe();
  }

  setSkin(skin: 'default' | 'night' | 'retro' | 'neon') {
    this.game.updateMe({ map_skin: skin }).subscribe({ error: (e) => this.msg.set(errMsg(e)) });
  }

  ownsSkin(s: string) {
    const me = this.game.me();
    return !!me && (me.is_premium || this.shop().some((i) => i.item === `skin:${s}` && i.owned));
  }

  buy(item: string) {
    this.game.buy(item).subscribe({
      next: () => {
        this.msg.set(`Bought ${this.itemName(item)}`);
        this.game.shop().subscribe((s) => this.shop.set(s));
      },
      error: (e) => this.msg.set(errMsg(e)),
    });
  }

  itemName(item: string) {
    const [kind, name] = item.split(':');
    if (kind === 'slot') return '+1 mission slot';
    return `${name} ${kind === 'skin' ? 'map skin' : 'markers'}`;
  }

  deleteAccount() {
    if (!confirm('Delete your account? All missions, check-ins and location data will be removed.')) return;
    this.game.deleteAccount().subscribe(() => this.auth.logout());
  }
}
