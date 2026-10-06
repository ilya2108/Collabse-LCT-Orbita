import { api } from '../client';
import type { GradeProfile, Industry, SkillGraph, Specialization } from '../types';

/** Справочник отраслей (`GET /skills/industries`). */
export function listIndustries(signal?: AbortSignal): Promise<Industry[]> {
  return api.get<Industry[]>('/skills/industries', { signal });
}

/** Специализации с версией графа и числом узлов (`GET /skills/specializations`). */
export function listSpecializations(signal?: AbortSignal): Promise<Specialization[]> {
  return api.get<Specialization[]>('/skills/specializations', { signal });
}

/** Граф специализации: слои, узлы, рёбра, мосты (`GET /skills/graph/{spec}`). */
export function fetchSkillGraph(spec: string, signal?: AbortSignal): Promise<SkillGraph> {
  return api.get<SkillGraph>(`/skills/graph/${encodeURIComponent(spec)}`, { signal });
}

/** Профили грейдов специализации (`GET /skills/grade-profiles/{spec}`). */
export function fetchGradeProfiles(spec: string, signal?: AbortSignal): Promise<GradeProfile[]> {
  return api.get<GradeProfile[]>(`/skills/grade-profiles/${encodeURIComponent(spec)}`, { signal });
}
