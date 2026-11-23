import { Component, effect, inject } from '@angular/core';
import { RouterLink, RouterLinkActive, RouterOutlet } from '@angular/router';
import { AuthService } from './core/auth.service';
import { GameService } from './core/game.service';
import { LocationService } from './core/location.service';
import { MissionPassedComponent } from './shared/mission-passed.component';

@Component({
  selector: 'app-root',
  imports: [RouterOutlet, RouterLink, RouterLinkActive, MissionPassedComponent],
  template: `
    <div class="flex h-dvh flex-col bg-base-100">
      <main class="relative min-h-0 flex-1 overflow-y-auto">
        <router-outlet />
      </main>

      @if (auth.loggedIn()) {
        <nav class="dock dock-sm relative border-t border-base-300 bg-base-200 hud-font">
          <a routerLink="/" routerLinkActive="dock-active" [routerLinkActiveOptions]="{ exact: true }">
            <span class="text-lg">⌖</span><span class="dock-label">Map</span>
          </a>
          <a routerLink="/missions" routerLinkActive="dock-active">
            <span class="text-lg">◆</span><span class="dock-label">Missions</span>
          </a>
          <a routerLink="/stories" routerLinkActive="dock-active">
            <span class="text-lg">★</span><span class="dock-label">Story</span>
          </a>
          <a routerLink="/social" routerLinkActive="dock-active">
            <span class="text-lg">☻</span><span class="dock-label">Crew</span>
          </a>
          <a routerLink="/profile" routerLinkActive="dock-active">
            <span class="text-lg">▤</span><span class="dock-label">Stats</span>
          </a>
        </nav>
      }
    </div>
    <app-mission-passed />
  `,
})
export class App {
  protected auth = inject(AuthService);
  private game = inject(GameService);
  private location = inject(LocationService);

  constructor() {
    effect(() => {
      if (this.auth.loggedIn()) {
        this.location.start();
        this.game.loadMe().subscribe();
      } else {
        this.location.stop();
      }
    });
  }
}
