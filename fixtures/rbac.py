"""RBAC Role based parametrization and checking

The purpose of this fixture is to allow tests to be run within the context of multiple different
users, without the hastle or modifying the test. To this end, the RBAC module and fixture do not
require any modifications to the test body.

The RBAC fixture starts by receiving a list of roles and associated errors from the ``rbac.csv``
file. This file looks similar to the one below

.. code-block:: csv

    node_name,evmgroup-administrator,evmgroup-auditor,default,evmgroup-super_administrator,evmgroup-operator
    cfme/tests/test_rbac.py/test_rbac,,ZeroDivisionError,MonkeyError,,ZeroDivisionError

Let's assume also we have a test that looks like the following::

    def test_rbac(rbac_role):
        if rbac_role != 'evmgroup-superadministrator' or rbac_role != 'evmgroup-operator':
            1 / 0

This csv defines the roles to be tested in row 0 and then each subsequent row defines the name of
a test followed by the exceptions that are expected for that particular test. In this way we can
have 5 states of test result.

 * **Test Passed** - This was expected - We do nothing to this and exit early. In the example above
   evmgroup-super_administrator fulfills this, as it expects no Exception.
 * **Test Failed** - This was expected - We consume the Exception and change the result of the test
   to be a pass. In the example, this is fulfilled by evmgroup-auditor as it was expected to fail
   with the ZeroDivisionError.
 * **Test Failed** - This was unexpected - We consume the Exception and raise another informing that
   the test should have passed. In the example above, evmgroup-administrator satisfies this
   condition as it didn't expect a failure, but got one.
 * **Test Failed** - This was expected, but the wrong Exception appeared - We consume the Exception
   throw another stating that the Exception wasn't of the expected type. In the example above, the
   default user satifies this as it receives the ZeroDivisionError, but expects MonkeyError.
 * **Test Passed** - This was unexpected - We have Exception to consume, but we raise an Exception
   of our own as the test should have failed. In the example above, evmgroup-operator satisfies
   this as it should have received the ZeroDivisionError, but actually passes with no error.

When a test is configured to run against RBAC suite, it will first parametrize the test with
the associated roles from the ``rbac.csv``. The test will then be wrapped and before it begins
we login as the *new* user. This process is also two fold. The ``pytest_store`` holds the current
user, and logging in is performed with whatever this user value is set to. So we first replace this
value with our new user. This ensures that if the browser fails during a force_naviagate, we get
the opportunity to log in again with the *right* user. Once the user is set, we attempt to login.

When the test finishes, we set the user back to ``default`` before moving on to handling the outcome
of the test with the wrapped hook handler. This ensures that the next test will have the correct
user at login, even if the test fails horribly, and even if the inspection of the outcome should
fail.

To configure a test to use RBAC is simple. It requires the importing of the RBAC imported roles
and the addition of this and the ldap configuration fixture to the prototype. Below is a complete
example of adding RBAC to a test::

    import pytest
    from fixtures.rbac import roles


    @pytest.mark.parametrize('rbac_role', roles)
    def test_rbac(rbac_role):
        if rbac_role != 'evmgroup-superadministrator' or rbac_role != 'evmgroup-operator':
            1 / 0

Exception matching is done with a simple string startswith match.

Currently there is no provision for skipping a role for a certain test.

"""
from utils.log import logger
from cfme.login import logout
from fixtures.pytest_store import set_user
from fixtures.artifactor_plugin import art_client, get_test_idents
from cfme.fixtures.pytest_selenium import take_screenshot
import pytest
import traceback
from utils.browser import browser, ensure_browser_open


last_user = "default"
old_user = None
enable_rbac = False


def save_traceback_file(node, contents):
    """A convenience function for artifactor file sending

    This function simply takes the nodes id and the contents of the file and processes
    them and sends them to artifactor

    Args:
        node: A pytest node
        contents: The contents of the traceback file
    """
    name, location = get_test_idents(node)
    art_client.fire_hook('filedump', test_location=location, test_name=name,
                         filename="rbac-traceback.txt",
                         contents=contents, fd_ident="rbac")


def save_screenshot(node, ss, sse):
    name, location = get_test_idents(node)
    if ss:
        art_client.fire_hook('filedump', test_location=location, test_name=name,
                             filename="rbac_screenshot.png", fd_ident="rbac_screenshot", mode="wb",
                             contents_base64=True, contents=ss)
    if sse:
        art_client.fire_hook('filedump', test_location=location, test_name=name,
                             filename="rbac_screenshot.txt", fd_ident="rbac_screenshot", mode="w",
                             contents_base64=False, contents=sse)


def really_logout():
    """A convenience function logging out

    This function simply ensures that we are logged out and that a new browser is loaded
    ready for use.
    """
    try:
        logout()
    except AttributeError:
        try:
            browser().quit()
        except AttributeError:
            ensure_browser_open()


@pytest.mark.hookwrapper
def pytest_pyfunc_call(pyfuncitem):
    """Inspects and consumes certain exceptions

    The guts of this function are explained above in the module documentation.

    Args:
        pyfuncitem: A pytest test item.
    """
    # do whatever you want before the next hook executes
    if not enable_rbac:
        yield
        return

    # Login as the "new" user to run the test under
    global last_user
    global old_user
    if 'rbac_role' in pyfuncitem.fixturenames:
        user = pyfuncitem._request.getfuncargvalue('rbac_role')
        really_logout()
        logger.info("setting user to {}".format(user))
        set_user(user)

    # Actually perform the test. outcome is set to be a result object from the test
    outcome = yield

    screenshot, screenshot_error = take_screenshot()

    # Set the user back again and log out
    if 'rbac_role' in pyfuncitem.fixturenames:
        really_logout()
        logger.info("setting user to default")
        set_user('default')

        # Handle the Exception
        logger.error(pyfuncitem.location[0])
        loc = "{}/{}".format(pyfuncitem.location[0], pyfuncitem.location[2])
        # loc = loc[:min([loc.rfind('['), len(loc)])]
        logger.error(loc)
        # errors = [v for k, v in tests.iteritems() if loc.startswith(k)]
        errors = pyfuncitem.function.meta.kwargs['from_docs']['rbac']['roles']
        if errors:
            # errors = errors[0]
            user = pyfuncitem.funcargs['rbac_role']
            if errors[user]:
                if not outcome.excinfo:
                    logger.error("RBAC: Test should fail!")
                    raise Exception("RBAC: You should fail!")
                else:
                    if outcome.excinfo[1].__repr__().startswith(errors[user]):
                        logger.info("RBAC: Test failed as expected")
                        outcome.force_result(True)
                    else:
                        contents = "".join(traceback.format_list(
                            traceback.extract_tb(outcome.excinfo[2])))
                        save_traceback_file(pyfuncitem, contents)
                        save_screenshot(pyfuncitem, screenshot, screenshot_error)
                        logger.error("RBAC: You blithering idiot, "
                                     "you failed with the wrong exception")
                        raise Exception("RBAC: You should fail with {}!".format(errors[user]))
            else:
                if not outcome.excinfo:
                    logger.info("RBAC: Test passed as expected")
                else:
                    logger.error("RBAC: Test should have passed!")
                    contents = "".join(traceback.format_list(
                        traceback.extract_tb(outcome.excinfo[2])))
                    save_traceback_file(pyfuncitem, contents)
                    save_screenshot(pyfuncitem, screenshot, screenshot_error)
                    raise Exception("RBAC: Test should have passed!")


@pytest.mark.hookwrapper
def pytest_generate_tests(metafunc):
    yield
    if 'rbac_role' in metafunc.fixturenames:
        if enable_rbac:
            try:
                meta_data = metafunc.function.meta
                roles = meta_data.kwargs['from_docs']['rbac']['roles'].keys()
            except:
                raise Exception("Test {} should have metadata describing RBAC roles")

        else:
            roles = ['default']
        metafunc.parametrize('rbac_role', roles)


def pytest_addoption(parser):
    # Create the cfme option group for use in other plugins
    parser.getgroup('cfme')
    parser.addoption("--rbac", action="store_true", default=False,
        help="enable rbac testing")


def pytest_configure(config):
    """ Filters the list of providers as part of pytest configuration. """
    global enable_rbac

    if config.getoption('rbac'):
        enable_rbac = True