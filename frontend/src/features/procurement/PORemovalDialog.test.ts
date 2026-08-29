// DDC-CWICR-OE: DataDrivenConstruction · OpenConstructionERP
// Copyright (c) 2026 Artem Boiko / DataDrivenConstruction
//
// Which removal verb a purchase order gets, and how a refused removal is read.
//
// Both are pure decisions the UI makes before any request goes out, and both
// are the kind of logic a type error cannot catch: `removalVerbFor` returning
// 'delete' for an issued PO would put a destructive button on a commercial
// document, and a `parseRemovalRefusal` that quietly returns null would turn
// the server's named blockers into a generic error box.

import { describe, it, expect } from 'vitest';

import { ApiError } from '@/shared/lib/api';

import { parseRemovalRefusal } from './api';
import { removalVerbFor } from './PORemovalDialog';

describe('removalVerbFor', () => {
  it('offers a real delete only for a draft', () => {
    expect(removalVerbFor('draft')).toBe('delete');
  });

  it('offers cancel for every status a supplier may have seen', () => {
    // Approval commits budget and issuing sends the number out, so from here
    // on the record and its number have to survive.
    expect(removalVerbFor('approved')).toBe('cancel');
    expect(removalVerbFor('issued')).toBe('cancel');
    expect(removalVerbFor('partially_received')).toBe('cancel');
  });

  it('offers nothing for a terminal purchase order', () => {
    // A completed PO records what was actually bought; a cancelled one has
    // already been taken back. Neither has a removal verb left.
    expect(removalVerbFor('completed')).toBeNull();
    expect(removalVerbFor('cancelled')).toBeNull();
  });

  it('never returns delete for anything but draft', () => {
    const statuses = [
      'approved',
      'issued',
      'partially_received',
      'completed',
      'cancelled',
    ];
    for (const status of statuses) {
      expect(removalVerbFor(status)).not.toBe('delete');
    }
  });
});

describe('parseRemovalRefusal', () => {
  const refusalBody = {
    detail: {
      code: 'purchase_order_has_dependents',
      message: 'Purchase order PO-004 cannot be deleted: 2 goods receipts refer to it.',
      remediation: 'Reverse or detach those records first.',
      holders: [
        { kind: 'goods_receipt', count: 2 },
        { kind: 'payable_invoice', count: 1 },
      ],
    },
  };

  it('reads the code, message and every holder out of a 409', () => {
    const parsed = parseRemovalRefusal(new ApiError(409, 'Conflict', refusalBody));
    expect(parsed).not.toBeNull();
    expect(parsed?.code).toBe('purchase_order_has_dependents');
    expect(parsed?.holders).toEqual([
      { kind: 'goods_receipt', count: 2 },
      { kind: 'payable_invoice', count: 1 },
    ]);
  });

  it('accepts a state refusal that names no holders', () => {
    // "Already cancelled" and "not deletable" carry an empty holders list -
    // the reason is the PO's own state, not something pointing at it.
    const parsed = parseRemovalRefusal(
      new ApiError(409, 'Conflict', {
        detail: {
          code: 'purchase_order_not_deletable',
          message: 'Purchase order PO-004 is in status issued.',
          remediation: 'Cancel it instead.',
          holders: [],
        },
      }),
    );
    expect(parsed?.code).toBe('purchase_order_not_deletable');
    expect(parsed?.holders).toEqual([]);
  });

  it('ignores a status that is not 409', () => {
    // A 403 from the permission gate must not be dressed up as a blocker
    // list; the caller falls back to the plain error message instead.
    expect(parseRemovalRefusal(new ApiError(403, 'Forbidden', refusalBody))).toBeNull();
  });

  it('ignores a 409 whose detail is a plain string', () => {
    // The module raises unstructured 409s elsewhere (the line-item replace
    // guard, for one). Those are not removal refusals.
    expect(
      parseRemovalRefusal(
        new ApiError(409, 'Conflict', { detail: 'Cannot replace line items' }),
      ),
    ).toBeNull();
  });

  it('ignores anything that is not an ApiError', () => {
    expect(parseRemovalRefusal(new Error('network down'))).toBeNull();
    expect(parseRemovalRefusal(null)).toBeNull();
    expect(parseRemovalRefusal(undefined)).toBeNull();
  });

  it('drops malformed holder entries instead of rendering them', () => {
    // A holder with no count would render a blank number next to a label,
    // which reads as "zero of these" - the opposite of what it means.
    const parsed = parseRemovalRefusal(
      new ApiError(409, 'Conflict', {
        detail: {
          code: 'purchase_order_has_dependents',
          message: 'blocked',
          remediation: '',
          holders: [
            { kind: 'goods_receipt', count: 2 },
            { kind: 'requisition' },
            null,
            'requisition',
          ],
        },
      }),
    );
    expect(parsed?.holders).toEqual([{ kind: 'goods_receipt', count: 2 }]);
  });
});
