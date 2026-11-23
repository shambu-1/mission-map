import { Component, OnInit, computed, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { ActivatedRoute } from '@angular/router';
import { errMsg, GameService } from '../core/game.service';
import { Friend, Mission, Squad } from '../core/models';
import { Place, PlacePickerComponent } from '../shared/place-picker.component';

@Component({
  selector: 'app-social',
  imports: [FormsModule, PlacePickerComponent],
  template: `
    <div class="mx-auto max-w-xl p-4 pb-24">
      <h1 class="hud-font mb-4 text-3xl font-bold">Crew</h1>

      <div role="tablist" class="tabs tabs-box mb-4">
        <button class="tab flex-1" [class.tab-active]="tab() === 'friends'" (click)="tab.set('friends')">Friends</button>
        <button class="tab flex-1" [class.tab-active]="tab() === 'board'" (click)="tab.set('board'); loadBoard()">Leaderboard</button>
        <button class="tab flex-1" [class.tab-active]="tab() === 'squads'" (click)="tab.set('squads')">Squads</button>
      </div>

      @if (msg()) {
        <div class="alert mb-3 py-2 text-sm">{{ msg() }}</div>
      }

      <!-- Friends -->
      @if (tab() === 'friends') {
        <div class="join mb-2 w-full">
          <input class="input join-item w-full" placeholder="Friend's username" [(ngModel)]="newFriend" (keydown.enter)="add()" />
          <button class="btn btn-primary join-item" (click)="add()">Add</button>
        </div>
        <button class="btn btn-ghost btn-xs mb-4" (click)="copyInvite()">Copy my invite link</button>

        @for (f of friends(); track f.user_id) {
          <div class="mb-2 flex items-center gap-2 rounded-box bg-base-200 p-3">
            <div class="flex-1">
              <div class="font-medium">{{ f.username }} <span class="badge badge-ghost badge-sm">Lv {{ f.level }}</span></div>
              <div class="text-xs text-base-content/60">
                @if (f.status === 'accepted') { {{ f.weekly_xp }} XP this week }
                @else if (f.direction === 'incoming') { wants to be friends }
                @else { request sent }
              </div>
            </div>
            @if (f.direction === 'incoming' && f.status === 'pending') {
              <button class="btn btn-success btn-sm" (click)="accept(f)">Accept</button>
            }
            @if (f.status === 'accepted' && canSend()) {
              <button class="btn btn-secondary btn-sm" (click)="sendTo.set(f)">Send mission</button>
            }
            <button class="btn btn-ghost btn-sm btn-circle" (click)="remove(f)" aria-label="Remove">✕</button>
          </div>
        } @empty {
          <div class="text-sm text-base-content/60">No friends yet. Friends see your completed missions and badges — never your live location.</div>
        }
        @if (!canSend()) {
          <p class="mt-3 text-xs text-base-content/50">🔒 Sending missions to friends unlocks at level 8.</p>
        }

        @if (sendTo(); as f) {
          <div class="mt-4 rounded-box border border-secondary p-3">
            <h3 class="hud-font mb-2 text-lg">Mission for {{ f.username }}</h3>
            <input class="input input-sm mb-2 w-full" placeholder="Try this café" [(ngModel)]="sendTitle" />
            <app-place-picker [(value)]="sendPlace" />
            <div class="mt-2 flex gap-2">
              <button class="btn btn-ghost btn-sm flex-1" (click)="sendTo.set(null)">Cancel</button>
              <button class="btn btn-secondary btn-sm flex-1" (click)="send(f)">Send</button>
            </div>
          </div>
        }
      }

      <!-- Leaderboard -->
      @if (tab() === 'board') {
        <p class="mb-2 text-xs text-base-content/60">Friends only, weekly XP. No global board.</p>
        <table class="table">
          <tbody>
            @for (row of board(); track row.user_id; let i = $index) {
              <tr [class.text-primary]="row.user_id === game.me()?.id">
                <td class="hud-font text-lg">#{{ i + 1 }}</td>
                <td>{{ row.username }}</td>
                <td>Lv {{ row.level }}</td>
                <td class="text-right font-mono">{{ row.weekly_xp }} XP</td>
              </tr>
            }
          </tbody>
        </table>
      }

      <!-- Squads -->
      @if (tab() === 'squads') {
        @if (!squadsUnlocked()) {
          <div class="alert">🔒 Squads and co-op missions unlock at level 10.</div>
        } @else {
          @for (s of squads(); track s.id) {
            <div class="mb-3 rounded-box bg-base-200 p-3">
              <div class="flex items-center gap-2">
                <h3 class="hud-font flex-1 text-xl font-bold">{{ s.name }}</h3>
                <button class="btn btn-ghost btn-xs" (click)="openSquad(s)">Missions</button>
                <button class="btn btn-ghost btn-xs text-error" (click)="leave(s)">Leave</button>
              </div>
              <div class="mt-1 flex flex-wrap gap-1">
                @for (m of s.members; track m.user_id) {
                  <span class="badge" [class.badge-primary]="m.role === 'leader'">{{ m.username }}</span>
                }
              </div>
              @if (openId() === s.id) {
                <div class="mt-3 border-t border-base-300 pt-3">
                  @for (m of squadMissions(); track m.id) {
                    <div class="flex justify-between py-1 text-sm">
                      <span>{{ m.title }}</span><span class="badge badge-sm">{{ m.status }}</span>
                    </div>
                  } @empty {
                    <div class="text-xs text-base-content/60">No co-op missions yet.</div>
                  }
                  @if (s.my_role === 'leader') {
                    <div class="mt-3 flex flex-col gap-2">
                      <input class="input input-sm w-full" placeholder="Squad trek on Sunday" [(ngModel)]="coopTitle" />
                      <label class="text-xs">Everyone must check in before:</label>
                      <input type="datetime-local" class="input input-sm w-full" [(ngModel)]="coopDeadline" />
                      <app-place-picker [(value)]="coopPlace" />
                      <button class="btn btn-primary btn-sm" (click)="createCoop(s)">Create co-op mission</button>
                    </div>
                  }
                </div>
              }
            </div>
          }
          <div class="mt-4 rounded-box border border-base-300 p-3">
            <h3 class="hud-font mb-2 text-lg">New squad (2–6 players)</h3>
            <input class="input input-sm mb-2 w-full" placeholder="Squad name" [(ngModel)]="squadName" />
            <input class="input input-sm mb-2 w-full" placeholder="Friends' usernames, comma separated" [(ngModel)]="squadMembers" />
            <button class="btn btn-primary btn-sm w-full" (click)="createSquad()">Create squad</button>
          </div>
        }
      }
    </div>
  `,
})
export class SocialPage implements OnInit {
  protected game = inject(GameService);
  private route = inject(ActivatedRoute);

  protected tab = signal<'friends' | 'board' | 'squads'>('friends');
  protected friends = signal<Friend[]>([]);
  protected board = signal<{ user_id: number; username: string; level: number; weekly_xp: number }[]>([]);
  protected squads = signal<Squad[]>([]);
  protected squadMissions = signal<Mission[]>([]);
  protected openId = signal<number | null>(null);
  protected sendTo = signal<Friend | null>(null);
  protected msg = signal<string | null>(null);

  protected canSend = computed(() => this.game.me()?.unlocks?.['send_to_friends'] === 'true');
  protected squadsUnlocked = computed(() => this.game.me()?.unlocks?.['squads'] === 'true');

  protected newFriend = '';
  protected sendTitle = '';
  protected sendPlace: Place | null = null;
  protected squadName = '';
  protected squadMembers = '';
  protected coopTitle = '';
  protected coopDeadline = '';
  protected coopPlace: Place | null = null;

  ngOnInit() {
    const add = this.route.snapshot.queryParamMap.get('add');
    if (add) {
      this.newFriend = add;
      this.add();
    }
    this.load();
  }

  load() {
    this.game.friends().subscribe((f) => this.friends.set(f));
    this.game.squads().subscribe((s) => this.squads.set(s));
  }

  loadBoard() {
    this.game.leaderboard().subscribe((b) => this.board.set(b));
  }

  add() {
    if (!this.newFriend.trim()) return;
    this.game.addFriend(this.newFriend.trim()).subscribe({
      next: (f) => {
        this.flash(f.status === 'accepted' ? `You and ${f.username} are now friends` : `Request sent to ${f.username}`);
        this.newFriend = '';
        this.load();
      },
      error: (e) => this.flash(errMsg(e)),
    });
  }

  accept(f: Friend) {
    this.game.acceptFriend(f.user_id).subscribe(() => this.load());
  }

  remove(f: Friend) {
    if (confirm(`Remove ${f.username}?`)) this.game.removeFriend(f.user_id).subscribe(() => this.load());
  }

  copyInvite() {
    const url = `${location.origin}/social?add=${this.game.me()?.username}`;
    navigator.clipboard?.writeText(url).then(() => this.flash('Invite link copied'));
  }

  send(f: Friend) {
    if (!this.sendTitle.trim() || !this.sendPlace) return this.flash('Add a title and a place');
    this.game
      .sendMission(f.user_id, {
        title: this.sendTitle,
        type: 'side',
        category: 'explore',
        lat: this.sendPlace.lat,
        lng: this.sendPlace.lng,
        place_name: this.sendPlace.name,
        radius_m: 100,
        dwell_min: 0,
        proof_required: false,
      })
      .subscribe({
        next: () => {
          this.flash(`Sent to ${f.username}'s inbox`);
          this.sendTo.set(null);
          this.sendTitle = '';
          this.sendPlace = null;
        },
        error: (e) => this.flash(errMsg(e)),
      });
  }

  createSquad() {
    const members = this.squadMembers.split(',').map((s) => s.trim()).filter(Boolean);
    this.game.createSquad(this.squadName, members).subscribe({
      next: () => {
        this.squadName = '';
        this.squadMembers = '';
        this.load();
      },
      error: (e) => this.flash(errMsg(e)),
    });
  }

  openSquad(s: Squad) {
    this.openId.set(this.openId() === s.id ? null : s.id);
    if (this.openId()) this.game.squadMissions(s.id).subscribe((m) => this.squadMissions.set(m));
  }

  leave(s: Squad) {
    if (confirm(`Leave ${s.name}?`)) this.game.leaveSquad(s.id).subscribe(() => this.load());
  }

  createCoop(s: Squad) {
    if (!this.coopTitle.trim() || !this.coopPlace || !this.coopDeadline) return this.flash('Title, place and time window are required');
    this.game
      .createSquadMission(s.id, {
        title: this.coopTitle,
        type: 'timed',
        category: 'social',
        lat: this.coopPlace.lat,
        lng: this.coopPlace.lng,
        place_name: this.coopPlace.name,
        radius_m: 150,
        dwell_min: 0,
        proof_required: false,
        deadline: new Date(this.coopDeadline).toISOString(),
      })
      .subscribe({
        next: () => {
          this.coopTitle = '';
          this.coopPlace = null;
          this.game.squadMissions(s.id).subscribe((m) => this.squadMissions.set(m));
        },
        error: (e) => this.flash(errMsg(e)),
      });
  }

  private flash(text: string) {
    this.msg.set(text);
    setTimeout(() => this.msg.set(null), 4000);
  }
}
