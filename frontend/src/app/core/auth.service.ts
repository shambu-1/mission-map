import { HttpClient } from '@angular/common/http';
import { Injectable, computed, inject, signal } from '@angular/core';
import { Router } from '@angular/router';
import { Observable, finalize, map, shareReplay, tap } from 'rxjs';
import { API } from './api';
import { Tokens } from './models';

const ACCESS = 'mm_access';
const REFRESH = 'mm_refresh';

@Injectable({ providedIn: 'root' })
export class AuthService {
  private http = inject(HttpClient);
  private router = inject(Router);

  private accessToken = signal<string | null>(localStorage.getItem(ACCESS));
  readonly loggedIn = computed(() => !!this.accessToken());
  private refreshing$: Observable<string> | null = null;

  get token(): string | null {
    return this.accessToken();
  }

  register(username: string, email: string, password: string) {
    return this.http.post<Tokens>(`${API}/auth/register`, { username, email, password }).pipe(tap((t) => this.store(t)));
  }

  login(username_or_email: string, password: string) {
    return this.http.post<Tokens>(`${API}/auth/login`, { username_or_email, password }).pipe(tap((t) => this.store(t)));
  }

  /** Refresh tokens rotate on every use; share one in-flight refresh between parallel 401s. */
  refresh(): Observable<string> {
    if (!this.refreshing$) {
      const refresh_token = localStorage.getItem(REFRESH) ?? '';
      this.refreshing$ = this.http.post<Tokens>(`${API}/auth/refresh`, { refresh_token }).pipe(
        tap((t) => this.store(t)),
        map((t) => t.access_token),
        finalize(() => (this.refreshing$ = null)),
        shareReplay(1),
      );
    }
    return this.refreshing$;
  }

  logout() {
    localStorage.removeItem(ACCESS);
    localStorage.removeItem(REFRESH);
    this.accessToken.set(null);
    this.router.navigateByUrl('/login');
  }

  private store(t: Tokens) {
    localStorage.setItem(ACCESS, t.access_token);
    localStorage.setItem(REFRESH, t.refresh_token);
    this.accessToken.set(t.access_token);
  }
}
