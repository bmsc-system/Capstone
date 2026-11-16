// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import PinSetupKeypad from './PinSetupKeypad';
import PinKeypad from './PinKeypad';

afterEach(cleanup);

async function type(pin: string) {
  for (const d of pin) {
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: new RegExp(`^${d}`) }));
    });
  }
}

describe('PinSetupKeypad', () => {
  it('saves when the same PIN is typed twice', async () => {
    const onSave = vi.fn().mockResolvedValue(undefined);
    render(<PinSetupKeypad onSave={onSave} />);
    await type('4826');
    expect(screen.getByText('Confirm your PIN')).toBeTruthy();
    await type('4826');
    expect(onSave).toHaveBeenCalledTimes(1);
    expect(onSave).toHaveBeenCalledWith('4826');
  });

  it('refuses an easy PIN on the first entry', async () => {
    const onSave = vi.fn();
    render(<PinSetupKeypad onSave={onSave} />);
    await type('1234');
    expect(screen.getByText('Create a PIN')).toBeTruthy();
    expect(screen.getByText(/too easy to guess/)).toBeTruthy();
    expect(onSave).not.toHaveBeenCalled();
  });

  it('after the server refuses, starts over instead of treating the old PIN as a new one', async () => {
    const onSave = vi.fn().mockRejectedValueOnce(new Error('Choose a less obvious PIN')).mockResolvedValue(undefined);
    render(<PinSetupKeypad onSave={onSave} />);
    await type('4826');
    await type('4826');
    expect(onSave).toHaveBeenCalledTimes(1);
    // Back on the first step with the reason shown, not silently on "Confirm".
    expect(screen.getByText('Create a PIN')).toBeTruthy();
    expect(screen.getByText(/Something went wrong/)).toBeTruthy();
    await type('5937');
    expect(screen.getByText('Confirm your PIN')).toBeTruthy();
    await type('5937');
    expect(onSave).toHaveBeenLastCalledWith('5937');
    expect(onSave).toHaveBeenCalledTimes(2);
  });
});

describe('PinKeypad', () => {
  it('sends a wrong PIN once, not again when the keypad unlocks', async () => {
    const onComplete = vi.fn();
    const { rerender } = render(<PinKeypad title="Enter your PIN" busy={false} failSignal={0} onComplete={onComplete} />);
    await type('4826');
    rerender(<PinKeypad title="Enter your PIN" busy failSignal={0} onComplete={onComplete} />);
    rerender(<PinKeypad title="Enter your PIN" busy={false} failSignal={1} onComplete={onComplete} />);
    expect(onComplete).toHaveBeenCalledTimes(1);
  });
});
