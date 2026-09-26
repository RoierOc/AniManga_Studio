export function healthSnapshotStatus({ health, integrity, healthError, integrityError }) {
  if (healthError || integrityError || !health || !integrity || health.ok !== true ||
      !Number.isFinite(health.problems) || integrity.available !== true ||
      !Array.isArray(integrity.mismatched)) {
    return 'incomplete'
  }
  if (health.problems > 0 || (integrity.mismatched?.length || 0) > 0) return 'issues'
  return 'healthy'
}
