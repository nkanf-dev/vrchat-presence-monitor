import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';

import { Pagination } from './Pagination';

describe('Pagination', () => {
  it('moves through pages and exposes its current range to the page input', async () => {
    const onPageChange = vi.fn();
    const user = userEvent.setup();
    render(
      <Pagination
        page={0}
        pageCount={5}
        busy={false}
        label="玩家"
        onPageChange={onPageChange}
      />,
    );

    const input = screen.getByRole('textbox', { name: '页码' });
    expect(input).toHaveAccessibleDescription('第 1 / 5 页');

    await user.click(screen.getByRole('button', { name: '玩家下一页' }));
    expect(onPageChange).toHaveBeenCalledWith(1);

    await user.clear(input);
    await user.type(input, '999{Enter}');
    expect(onPageChange).toHaveBeenLastCalledWith(4);
    expect(input).toHaveValue('5');
  });
});
