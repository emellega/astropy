# Licensed under a 3-clause BSD style license - see LICENSE.rst

from __future__ import annotations

import copy
from dataclasses import dataclass, field, fields, replace
from typing import Any, Sequence

import astropy.units as u
from astropy.utils.compat import PYTHON_LT_3_10

from ._converter import _REGISTRY_FVALIDATORS, FValidateCallable, _register_validator

__all__ = []


if not PYTHON_LT_3_10:
    from dataclasses import KW_ONLY
else:
    KW_ONLY = Any


@dataclass(frozen=True)
class _UnitField:
    # TODO: rm this class when py3.13+ allows for `field(converter=...)`

    def __get__(
        self, obj: Parameter | None, objcls: type[Parameter] | None
    ) -> u.Unit | None:
        if obj is None:  # calling `Parameter.unit` from the class
            return None
        return getattr(obj, "_unit", None)

    def __set__(self, obj: Parameter, value: Any) -> None:
        object.__setattr__(obj, "_unit", u.Unit(value) if value is not None else None)


@dataclass(frozen=True)
class _FValidateField:
    default: FValidateCallable | str = "default"

    def __get__(
        self, obj: Parameter | None, objcls: type[Parameter] | None
    ) -> FValidateCallable | str:
        if obj is None:  # calling `Parameter.fvalidate` from the class
            return self.default
        return obj._fvalidate  # calling `Parameter.fvalidate` from an instance

    def __set__(self, obj: Parameter, value: Any) -> None:
        # Always store input fvalidate.
        object.__setattr__(obj, "_fvalidate_in", value)

        # Process to the callable.
        if value in _REGISTRY_FVALIDATORS:
            value = _REGISTRY_FVALIDATORS[value]
        elif isinstance(value, str):
            msg = f"`fvalidate`, if str, must be in {_REGISTRY_FVALIDATORS.keys()}"
            raise ValueError(msg)
        elif not callable(value):
            msg = f"`fvalidate` must be a function or {_REGISTRY_FVALIDATORS.keys()}"
            raise TypeError(msg)
        object.__setattr__(obj, "_fvalidate", value)


@dataclass(frozen=True)
class Parameter:
    r"""Cosmological parameter (descriptor).

    Should only be used with a :class:`~astropy.cosmology.Cosmology` subclass.

    Parameters
    ----------
    derived : bool (optional, keyword-only)
        Whether the Parameter is 'derived', default `False`.
        Derived parameters behave similarly to normal parameters, but are not
        sorted by the |Cosmology| signature (probably not there) and are not
        included in all methods. For reference, see ``Ode0`` in
        ``FlatFLRWMixin``, which removes :math:`\Omega_{de,0}`` as an
        independent parameter (:math:`\Omega_{de,0} \equiv 1 - \Omega_{tot}`).
    unit : unit-like or None (optional, keyword-only)
        The `~astropy.units.Unit` for the Parameter. If None (default) no
        unit as assumed.
    equivalencies : `~astropy.units.Equivalency` or sequence thereof
        Unit equivalencies for this Parameter.
    fvalidate : callable[[object, object, Any], Any] or str (optional, keyword-only)
        Function to validate the Parameter value from instances of the
        cosmology class. If "default", uses default validator to assign units
        (with equivalencies), if Parameter has units.
        For other valid string options, see ``Parameter._registry_validators``.
        'fvalidate' can also be set through a decorator with
        :meth:`~astropy.cosmology.Parameter.validator`.
    doc : str or None (optional, keyword-only)
        Parameter description.

    Examples
    --------
    For worked examples see :class:`~astropy.cosmology.FLRW`.
    """

    if not PYTHON_LT_3_10:
        _: KW_ONLY

    derived: bool = False
    """Whether the Parameter can be set, or is derived, on the cosmology."""

    # Units
    unit: _UnitField = _UnitField()  # noqa: RUF009
    """The unit of the Parameter (can be `None` for unitless)."""

    equivalencies: u.Equivalency | Sequence[u.Equivalency] = field(default_factory=list)
    """Unit equivalencies available when setting the parameter."""

    # Setting
    fvalidate: _FValidateField = _FValidateField(default="default")  # noqa: RUF009
    """Function to validate/convert values when setting the Parameter."""

    # Info
    doc: str | None = None
    """Parameter description."""

    if PYTHON_LT_3_10:

        def __init__(
            self,
            *,
            derived=False,
            unit=None,
            equivalencies=[],
            fvalidate="default",
            doc=None,
        ):
            object.__setattr__(self, "derived", derived)
            vars(type(self))["unit"].__set__(self, unit)
            object.__setattr__(self, "equivalencies", equivalencies)
            vars(type(self))["fvalidate"].__set__(self, fvalidate)
            object.__setattr__(self, "doc", doc)

            self.__post_init__()

    def __post_init__(self) -> None:
        self._fvalidate_in: FValidateCallable | str
        self._fvalidate: FValidateCallable
        object.__setattr__(self, "__doc__", self.doc)
        # attribute name on container cosmology class.
        # really set in __set_name__, but if Parameter is not init'ed as a
        # descriptor this ensures that the attributes exist.
        object.__setattr__(self, "_attr_name", None)
        object.__setattr__(self, "_attr_name_private", None)

    def __set_name__(self, cosmo_cls: type, name: str) -> None:
        # attribute name on container cosmology class
        self._attr_name: str
        self._attr_name_private: str
        object.__setattr__(self, "_attr_name", name)
        object.__setattr__(self, "_attr_name_private", "_" + name)

    @property
    def name(self):
        """Parameter name."""
        return self._attr_name

    # -------------------------------------------
    # descriptor and property-like methods

    def __get__(self, cosmology, cosmo_cls=None):
        # Get from class
        if cosmology is None:
            return self
        # Get from instance
        return getattr(cosmology, self._attr_name_private)

    def __set__(self, cosmology, value):
        """Allows attribute setting once. Raises AttributeError subsequently."""
        # Raise error if setting 2nd time.
        if hasattr(cosmology, self._attr_name_private):
            raise AttributeError(f"can't set attribute {self._attr_name} again")

        # Validate value, generally setting units if present
        value = self.validate(cosmology, copy.deepcopy(value))

        # Make the value read-only, if ndarray-like
        if hasattr(value, "setflags"):
            value.setflags(write=False)

        # Set the value on the cosmology
        setattr(cosmology, self._attr_name_private, value)

    # -------------------------------------------
    # validate value

    def validator(self, fvalidate):
        """Make new Parameter with custom ``fvalidate``.

        Note: ``Parameter.fvalidator`` must be the top-most descriptor decorator.

        Parameters
        ----------
        fvalidate : callable[[type, type, Any], Any]

        Returns
        -------
        `~astropy.cosmology.Parameter`
            Copy of this Parameter but with custom ``fvalidate``.
        """
        return self.clone(fvalidate=fvalidate)

    def validate(self, cosmology, value):
        """Run the validator on this Parameter.

        Parameters
        ----------
        cosmology : `~astropy.cosmology.Cosmology` instance
        value : Any
            The object to validate.

        Returns
        -------
        Any
            The output of calling ``fvalidate(cosmology, self, value)``
            (yes, that parameter order).
        """
        return self._fvalidate(cosmology, self, value)

    @staticmethod
    def register_validator(key, fvalidate=None):
        """Decorator to register a new kind of validator function.

        Parameters
        ----------
        key : str
        fvalidate : callable[[object, object, Any], Any] or None, optional
            Value validation function.

        Returns
        -------
        ``validator`` or callable[``validator``]
            if validator is None returns a function that takes and registers a
            validator. This allows ``register_validator`` to be used as a
            decorator.
        """
        return _register_validator(key, fvalidate=fvalidate)

    # -------------------------------------------

    def clone(self, **kw):
        """Clone this `Parameter`, changing any constructor argument.

        Parameters
        ----------
        **kw
            Passed to constructor. The current values, eg. ``fvalidate`` are
            used as the default values, so an empty ``**kw`` is an exact copy.

        Examples
        --------
        >>> p = Parameter()
        >>> p
        Parameter(derived=False, unit=None, equivalencies=[],
                  fvalidate='default', doc=None)

        >>> p.clone(unit="km")
        Parameter(derived=False, unit=Unit("km"), equivalencies=[],
                  fvalidate='default', doc=None)
        """
        kw.setdefault("fvalidate", self._fvalidate_in)  # prefer the input fvalidate
        cloned = replace(self, **kw)
        # Transfer over the __set_name__ stuff. If `clone` is used to make a
        # new descriptor, __set_name__ will be called again, overwriting this.
        object.__setattr__(cloned, "_attr_name", self._attr_name)
        object.__setattr__(cloned, "_attr_name_private", self._attr_name_private)

        return cloned

    def __repr__(self) -> str:
        """Return repr(self)."""
        fields_repr = (
            f"{f.name}={(getattr(self, f.name if f.name != 'fvalidate' else '_fvalidate_in'))!r}"
            for f in fields(self)
            if f.repr
        )
        return f"{self.__class__.__name__}({', '.join(fields_repr)})"
