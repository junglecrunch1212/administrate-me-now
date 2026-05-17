"""Adapter pack scaffold templates.

This is a package marker so ``adapters_scaffold/templates/`` ships
alongside the CLI module. Templates live as data files (``.j2`` for
substitution, raw for verbatim copy) under ``templates/<kind-lower>-<runtime>/``;
the loader at :func:`adminme.cli.commands.adapters._render_template`
copies them into the scaffold output with ``<NAME>`` / ``<CLASSNAME>``
substitution.
"""
