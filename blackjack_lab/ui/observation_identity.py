"""Disposable display identity, never a saved prefix or a second card ledger."""
from ..analysis.contracts import digest
from ..analysis.sidebets.information import window
from ..ledger.card_inventory import project_prepared, original_cards
from ..ledger.events import UNDO, CORRECTION


def observed_identity(ledger, current, seat, profile):
    # The caller obtains current from the validated callback read_frame. Only the
    # current shoe affects its physical inventory; all controls are retained so
    # corrections recorded after a shoe change and their undo still apply.
    prefix = [e.to_dict() for e in ledger.events
              if e.shoe_id == current.shoe_id or e.etype in (UNDO, CORRECTION)]
    inventory = project_prepared(ledger.session_id, ledger.events[-1].seq, prefix, current)
    invalid = ((inventory['reason_code'], inventory['reason'])
               if inventory['status'] == 'invalid' else None)
    return digest(dict(session=ledger.session_id, window=window(current), seat=seat,
        participating=seat in current.table.participants, profile=profile.to_dict(),
        originals=original_cards(inventory, current.round_id, seat), invalid=invalid))
