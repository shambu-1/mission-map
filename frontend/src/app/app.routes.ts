import { inject } from '@angular/core';
import { CanActivateFn, Router, Routes } from '@angular/router';
import { AuthService } from './core/auth.service';

const authed: CanActivateFn = () => inject(AuthService).loggedIn() || inject(Router).parseUrl('/login');
const guest: CanActivateFn = () => !inject(AuthService).loggedIn() || inject(Router).parseUrl('/');

export const routes: Routes = [
  { path: 'login', canActivate: [guest], loadComponent: () => import('./pages/login.page').then((m) => m.LoginPage) },
  { path: '', canActivate: [authed], loadComponent: () => import('./pages/map.page').then((m) => m.MapPage) },
  { path: 'missions/new', canActivate: [authed], loadComponent: () => import('./pages/mission-form.page').then((m) => m.MissionFormPage) },
  { path: 'missions/:id/edit', canActivate: [authed], loadComponent: () => import('./pages/mission-form.page').then((m) => m.MissionFormPage) },
  { path: 'missions', canActivate: [authed], loadComponent: () => import('./pages/missions.page').then((m) => m.MissionsPage) },
  { path: 'stories', canActivate: [authed], loadComponent: () => import('./pages/stories.page').then((m) => m.StoriesPage) },
  { path: 'social', canActivate: [authed], loadComponent: () => import('./pages/social.page').then((m) => m.SocialPage) },
  { path: 'profile', canActivate: [authed], loadComponent: () => import('./pages/profile.page').then((m) => m.ProfilePage) },
  { path: '**', redirectTo: '' },
];
