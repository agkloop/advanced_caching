from advanced_caching import BGCache
import inspect


is_not_testing = True


def get_loader_fn():
    caller_stack_trace = inspect.stack()
    stack_fn_names_list = [frame.function for frame in caller_stack_trace]

    print("get_loader_fn called, setting up loader...")

    @BGCache.register_loader(
        "all_filter_keys_catalog_loader",
        interval_seconds=60 * 60 * 1 * is_not_testing,
        run_immediately=True,
    )
    def get_all_filter_keys_catalog():
        print("Loading all filter keys catalog...", stack_fn_names_list)
        return "D"

    return get_all_filter_keys_catalog


def call_fn():
    return get_loader_fn()


if __name__ == "__main__":
    for i in range(10):
        print(i, call_fn()())
