import random

from fakeredis import _msgs as msgs
from fakeredis._commands import (command, Key, Int, CommandItem)
from fakeredis._helpers import (OK, SimpleError, casematch)


def _calc_setop(op, stop_if_missing, key, *keys):
    if stop_if_missing and not key.value:
        return set()
    value = key.value
    if not isinstance(value, set):
        raise SimpleError(msgs.WRONGTYPE_MSG)
    ans = value.copy()
    for other in keys:
        value = other.value if other.value is not None else set()
        if not isinstance(value, set):
            raise SimpleError(msgs.WRONGTYPE_MSG)
        if stop_if_missing and not value:
            return set()
        ans = op(ans, value)
    return ans


def _setop(op, stop_if_missing, dst, key, *keys):
    """Apply one of SINTER[STORE], SUNION[STORE], SDIFF[STORE].

    If `stop_if_missing`, the output will be made an empty set as soon as
    an empty input set is encountered (use for SINTER[STORE]). May assume
    that `key` is a set (or empty), but `keys` could be anything.
    """
    ans = _calc_setop(op, stop_if_missing, key, *keys)
    if dst is None:
        return list(ans)
    else:
        dst.value = ans
        return len(dst.value)


class SetCommandsMixin:
    # Set and Hyperloglog commands

    # Set commands
    @command((Key(set), bytes), (bytes,))
    def sadd(self, key, *members):
        old_size = len(key.value)
        key.value.update(members)
        key.updated()
        return len(key.value) - old_size

    @command((Key(set),))
    def scard(self, key):
        return len(key.value)

    @command((Key(set),), (Key(set),))
    def sdiff(self, *keys):
        return _setop(lambda a, b: a - b, False, None, *keys)

    @command((Key(), Key(set)), (Key(set),))
    def sdiffstore(self, dst, *keys):
        return _setop(lambda a, b: a - b, False, dst, *keys)

    @command((Key(set),), (Key(set),))
    def sinter(self, *keys):
        res = _setop(lambda a, b: a & b, True, None, *keys)
        return res

    @command((Int, bytes), (bytes,))
    def sintercard(self, numkeys, *args):
        if self.version < (7,):
            raise SimpleError(msgs.UNKNOWN_COMMAND_MSG.format('sintercard'))
        if numkeys < 1:
            raise SimpleError(msgs.SYNTAX_ERROR_MSG)
        limit = 0
        if casematch(args[-2], b'limit'):
            limit = Int.decode(args[-1])
            args = args[:-2]
        if numkeys != len(args):
            raise SimpleError(msgs.SYNTAX_ERROR_MSG)
        keys = [CommandItem(args[i], self._db, item=self._db.get(args[i], default=None))
                for i in range(numkeys)]

        res = _setop(lambda a, b: a & b, False, None, *keys)
        return len(res) if limit == 0 else min(limit, len(res))

    @command((Key(), Key(set)), (Key(set),))
    def sinterstore(self, dst, *keys):
        return _setop(lambda a, b: a & b, True, dst, *keys)

    @command((Key(set), bytes))
    def sismember(self, key, member):
        return int(member in key.value)

    @command((Key(set), bytes), (bytes,))
    def smismember(self, key, *members):
        return [self.sismember(key, member) for member in members]

    @command((Key(set),))
    def smembers(self, key):
        return list(key.value)

    @command((Key(set, 0), Key(set), bytes))
    def smove(self, src, dst, member):
        try:
            src.value.remove(member)
            src.updated()
        except KeyError:
            return 0
        else:
            dst.value.add(member)
            dst.updated()  # TODO: is it updated if member was already present?
            return 1

    @command((Key(set),), (Int,))
    def spop(self, key, count=None):
        if count is None:
            if not key.value:
                return None
            item = random.sample(list(key.value), 1)[0]
            key.value.remove(item)
            key.updated()
            return item
        else:
            if count < 0:
                raise SimpleError(msgs.INDEX_ERROR_MSG)
            items = self.srandmember(key, count)
            for item in items:
                key.value.remove(item)
                key.updated()  # Inside the loop because redis special-cases count=0
            return items

    @command((Key(set),), (Int,))
    def srandmember(self, key, count=None):
        if count is None:
            if not key.value:
                return None
            else:
                return random.sample(list(key.value), 1)[0]
        elif count >= 0:
            count = min(count, len(key.value))
            return random.sample(list(key.value), count)
        else:
            items = list(key.value)
            return [random.choice(items) for _ in range(-count)]

    @command((Key(set), bytes), (bytes,))
    def srem(self, key, *members):
        old_size = len(key.value)
        for member in members:
            key.value.discard(member)
        deleted = old_size - len(key.value)
        if deleted:
            key.updated()
        return deleted

    @command((Key(set), Int), (bytes, bytes))
    def sscan(self, key, cursor, *args):
        return self._scan(key.value, cursor, *args)

    @command((Key(set),), (Key(set),))
    def sunion(self, *keys):
        return _setop(lambda a, b: a | b, False, None, *keys)

    @command((Key(), Key(set)), (Key(set),))
    def sunionstore(self, dst, *keys):
        return _setop(lambda a, b: a | b, False, dst, *keys)

    # Hyperloglog commands
    # These are not quite the same as the real redis ones, which are
    # approximate and store the results in a string. Instead, it is implemented
    # on top of sets.

    @command((Key(set),), (bytes,))
    def pfadd(self, key, *elements):
        result = self.sadd(key, *elements)
        # Per the documentation:
        # - 1 if at least 1 HyperLogLog internal register was altered. 0 otherwise.
        return 1 if result > 0 else 0

    @command((Key(set),), (Key(set),))
    def pfcount(self, *keys):
        """
        Return the approximated cardinality of
        the set observed by the HyperLogLog at key(s).
        """
        return len(self.sunion(*keys))

    @command((Key(set), Key(set)), (Key(set),))
    def pfmerge(self, dest, *sources):
        """Merge N different HyperLogLogs into a single one."""
        self.sunionstore(dest, *sources)
        return OK
