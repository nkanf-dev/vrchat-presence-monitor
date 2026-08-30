import { describe, expect, it } from 'vitest';

import {
  activityDetail,
  coverageLabel,
  locationLabel,
  ratioLabel,
  stableWorldColor,
} from './intelligence';

describe('intelligence presentation', () => {
  it('keeps unavailable activity distinct from zero', () => {
    expect(ratioLabel(null)).toBe('—');
    expect(ratioLabel(0)).toBe('0%');
    expect(activityDetail({
      ratio: null,
      online_minutes: 0,
      observed_minutes: 12,
      eligible_minutes: 60,
    })).toBe('已记录 12 分钟，暂不足以计算');
  });

  it('uses a concrete world name when it is known', () => {
    expect(locationLabel('world', 'English Hub')).toBe('English Hub');
    expect(locationLabel('private')).toBe('私人位置');
    expect(locationLabel('gap')).toBe('记录中断');
  });

  it('summarizes recording coverage in user language', () => {
    expect(coverageLabel(0, 60)).toBe('这个时段还没有记录');
    expect(coverageLabel(58, 60)).toBe('记录完整');
  });

  it('assigns deterministic world colors', () => {
    const first = stableWorldColor('wrld_first');
    expect(stableWorldColor('wrld_first')).toBe(first);
    expect(stableWorldColor('wrld_second')).not.toBe(first);
  });
});
