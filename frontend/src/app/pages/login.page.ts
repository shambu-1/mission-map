import { Component, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Router } from '@angular/router';
import { AuthService } from '../core/auth.service';
import { errMsg } from '../core/game.service';

@Component({
  selector: 'app-login',
  imports: [FormsModule],
  template: `
    <div class="flex min-h-full items-center justify-center p-6">
      <div class="w-full max-w-sm">
        <h1 class="hud-font mb-1 text-center text-5xl font-bold text-primary">MISSION MAP</h1>
        <p class="mb-8 text-center text-sm text-base-content/60">Your city is the game map.</p>

        <div role="tablist" class="tabs tabs-box mb-4">
          <button role="tab" class="tab flex-1" [class.tab-active]="mode() === 'login'" (click)="mode.set('login')">Log in</button>
          <button role="tab" class="tab flex-1" [class.tab-active]="mode() === 'register'" (click)="mode.set('register')">
            New player
          </button>
        </div>

        <form class="flex flex-col gap-3" (ngSubmit)="submit()">
          <input class="input w-full" name="username" placeholder="Username" autocomplete="username" [(ngModel)]="username" required />
          @if (mode() === 'register') {
            <input class="input w-full" name="email" type="email" placeholder="Email" autocomplete="email" [(ngModel)]="email" required />
          }
          <input
            class="input w-full"
            name="password"
            type="password"
            placeholder="Password (8+ characters)"
            autocomplete="current-password"
            [(ngModel)]="password"
            required
          />
          @if (error()) {
            <div class="alert alert-error text-sm">{{ error() }}</div>
          }
          <button class="btn btn-primary hud-font text-lg" [disabled]="busy()">
            {{ mode() === 'login' ? 'START' : 'CREATE PLAYER' }}
          </button>
        </form>
      </div>
    </div>
  `,
})
export class LoginPage {
  private auth = inject(AuthService);
  private router = inject(Router);

  mode = signal<'login' | 'register'>('login');
  busy = signal(false);
  error = signal<string | null>(null);
  username = '';
  email = '';
  password = '';

  submit() {
    this.busy.set(true);
    this.error.set(null);
    const req =
      this.mode() === 'login'
        ? this.auth.login(this.username, this.password)
        : this.auth.register(this.username, this.email, this.password);
    req.subscribe({
      next: () => this.router.navigateByUrl('/'),
      error: (e) => {
        this.error.set(errMsg(e));
        this.busy.set(false);
      },
    });
  }
}
